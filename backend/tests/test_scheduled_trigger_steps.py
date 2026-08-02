import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import OperationalError, close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from builders.currencies import create_currency
from builders.models import ItemDefinition, MobDefinition, Trigger
from config import constants as adv_consts
from core.economy import MAX_CURRENCY_AMOUNT
from core.scoped_state import STATE_SCOPE_CHARACTER, get_state_snapshot
from core.trigger_steps import (
    SCRIPT_COMMAND_DEPTH_KEY,
    SCRIPT_COMMAND_PROVENANCE_KEY,
    TriggerStepSpecError,
    normalize_trigger_steps,
)
from spawns.events import GameEvent, publish_events
from spawns.handlers.registry import dispatch_command
from spawns.models import (
    CombatEncounter,
    GameEventOutbox,
    Item,
    Mob,
    MobState,
    Player,
    PlayerCurrencyBalance,
    ScheduledTriggerRun,
)
from spawns.trigger_steps import (
    MAX_ACTIVE_TRIGGER_RUNS_PER_ACTOR,
    MAX_TRIGGER_SET_MOB_CANDIDATES,
    TriggerStepStartResult,
    TriggerStepExecutionError,
    TriggerMobChange,
    TriggerMobChanges,
    _consume_room_item,
    _mob_change_events,
    _prelock_step_resources,
    advance_due_trigger_run,
    process_due_trigger_runs,
    prune_terminal_trigger_runs,
    start_trigger_steps,
)
from spawns.triggers import execute_command_fallback_trigger
from spawns.script_commands import MAX_SCRIPT_COMMAND_DEPTH
from spawns.wallet import mutate_balances
from tests.base import WorldTestCase
from worlds.models import Room, World, WorldConfig
from tests.utils import capture_game_messages, dispatch_text_command


class TestScheduledTriggerSteps(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.player.in_game = True
        self.player.save(update_fields=["in_game"])
        self.seed = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seed",
            name="a barley seed",
        )
        self.seedling = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seedling",
            name="a barley seedling",
        )
        self.growing = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-growing",
            name="a growing barley plant",
        )
        self.mature = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-mature",
            name="a mature barley plant",
        )
        self.harvested = ItemDefinition.objects.create(
            world=self.world,
            slug="harvested-barley",
            name="a bunch of harvested barley",
        )

    def _steps(self):
        return [
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "consume_item",
                        "actor": "trigger_actor",
                        "item": "itemdefinition.barley-seed",
                        "count": 1,
                    },
                    {
                        "type": "spawn_room_item",
                        "room": "trigger_room",
                        "item": "itemdefinition.barley-seedling",
                        "bind": "crop",
                    },
                    {
                        "type": "echo",
                        "room": "trigger_room",
                        "text": "A soft rustle captures your attention.",
                    },
                ],
            },
            {
                "after_seconds": 20,
                "actions": [
                    {
                        "type": "replace_room_item",
                        "target": "crop",
                        "with": "itemdefinition.barley-growing",
                    },
                    {
                        "type": "echo",
                        "room": "trigger_room",
                        "text": "A murmur of growth fills the air.",
                    },
                ],
            },
            {
                "after_seconds": 20,
                "actions": [
                    {
                        "type": "replace_room_item",
                        "target": "crop",
                        "with": "itemdefinition.barley-mature",
                    },
                    {
                        "type": "echo",
                        "room": "trigger_room",
                        "text": "The barley reaches harvest-ready splendor.",
                    },
                ],
            },
        ]

    def _conditions(self):
        return json.dumps({
            "all": [
                {
                    "item_present": {
                        "location": "actor_inventory",
                        "item": "itemdefinition.barley-seed",
                    },
                },
                {
                    "not": {
                        "any": [
                            {
                                "item_present": {
                                    "location": "room",
                                    "item": "itemdefinition.barley-seedling",
                                },
                            },
                            {
                                "item_present": {
                                    "location": "room",
                                    "item": "itemdefinition.barley-growing",
                                },
                            },
                            {
                                "item_present": {
                                    "location": "room",
                                    "item": "itemdefinition.barley-mature",
                                },
                            },
                        ],
                    },
                },
            ],
        })

    def _harvest_steps(self):
        return [
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "consume_room_item",
                        "room": "trigger_room",
                        "item": "itemdefinition.barley-mature",
                    },
                    {
                        "type": "grant_item",
                        "actor": "trigger_actor",
                        "item": "itemdefinition.harvested-barley",
                    },
                    {
                        "type": "echo",
                        "room": "trigger_room",
                        "text": "You gather a bunch of harvested barley.",
                    },
                ],
            },
        ]

    def _harvest_conditions(self):
        return json.dumps({
            "item_present": {
                "location": "room",
                "item": "itemdefinition.barley-mature",
            },
        })

    def _create_trigger(
        self,
        *,
        steps=None,
        conditions=None,
        gate_delay=0,
        world=None,
        room=None,
        match="plant seed",
    ):
        trigger_world = world or self.world
        trigger_room = room or self.room
        return Trigger.objects.create(
            world=trigger_world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=trigger_room.id,
            name="Plant barley",
            match=match,
            script="",
            steps=steps if steps is not None else self._steps(),
            conditions=conditions if conditions is not None else self._conditions(),
            gate_delay=gate_delay,
            display_action_in_room=True,
        )

    def _inventory_condition(self, *, count=1):
        return json.dumps({
            "item_present": {
                "location": "actor_inventory",
                "item": "itemdefinition.barley-seed",
                "count": count,
            },
        })

    def _room_items(self, definition):
        return Item.objects.filter(
            world=self.spawn_world,
            container_type=ContentType.objects.get_for_model(Room),
            container_id=self.room.id,
            definition=definition,
            is_pending_deletion=False,
        )

    def _dispatch(self, player_id, command="plant seed"):
        with self.captureOnCommitCallbacks(execute=True):
            dispatch_text_command(player_id, command)

    def _cross_instance_mob_event_fixture(self, *, event, enter_event=False):
        instance_config = WorldConfig.objects.create()
        instance_template = World.objects.new_world(
            name="Shared Barley Cellar",
            author=self.user,
            config=instance_config,
            instance_of=self.world,
        )
        origin_room = instance_template.rooms.first()
        event_room = origin_room.create_at("north") if enter_event else origin_room
        actor_runtime = instance_template.create_spawn_world(instance_ref="actor-run")
        other_runtime = instance_template.create_spawn_world(instance_ref="other-run")
        mob_definition = MobDefinition.objects.create(
            world=self.world,
            name="Barley Keeper",
        )
        actor_runtime_mob = Mob.objects.create(
            world=actor_runtime,
            room=event_room,
            definition=mob_definition,
            name="Actor-run Barley Keeper",
        )
        other_runtime_mob = Mob.objects.create(
            world=other_runtime,
            room=event_room,
            definition=mob_definition,
            name="Other-run Barley Keeper",
        )
        trigger = Trigger.objects.create(
            world=instance_template,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=mob_definition.id,
            name="Keeper notices the player",
            event=event,
            match="grow barley" if event == adv_consts.MOB_REACTION_EVENT_SAYING else "",
            script="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The keeper notices.",
                        },
                    ],
                },
            ],
            gate_delay=0,
            display_action_in_room=False,
        )
        self.player.world = actor_runtime
        self.player.room = origin_room
        self.player.stamina = 100
        self.player.save(update_fields=["world", "room", "stamina"])
        return SimpleNamespace(
            trigger=trigger,
            origin_room=origin_room,
            event_room=event_room,
            actor_runtime=actor_runtime,
            other_runtime=other_runtime,
            actor_runtime_mob=actor_runtime_mob,
            other_runtime_mob=other_runtime_mob,
        )

    def test_harvest_actions_normalize_default_and_explicit_counts(self):
        normalized = normalize_trigger_steps([
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "consume_room_item",
                        "room": "trigger_room",
                        "item": "itemdefinition.barley-mature",
                    },
                    {
                        "type": "grant_item",
                        "actor": "trigger_actor",
                        "item": "itemdefinition.harvested-barley",
                        "count": 2,
                    },
                ],
            },
        ])

        self.assertEqual(
            normalized[0]["actions"],
            [
                {
                    "type": "consume_room_item",
                    "room": "trigger_room",
                    "item": "itemdefinition.barley-mature",
                    "count": 1,
                },
                {
                    "type": "grant_item",
                    "actor": "trigger_actor",
                    "item": "itemdefinition.harvested-barley",
                    "count": 2,
                },
            ],
        )

    def test_send_actions_normalize_trigger_actor_and_text(self):
        normalized = normalize_trigger_steps([
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "send",
                        "actor": "trigger_actor",
                        "text": "  You pull the lever.  ",
                    },
                    {
                        "type": "send_except",
                        "actor": "trigger_actor",
                        "text": "  {{ actor }} pulls the lever.  ",
                    },
                ],
            },
        ])

        self.assertEqual(
            normalized[0]["actions"],
            [
                {
                    "type": "send",
                    "actor": "trigger_actor",
                    "text": "You pull the lever.",
                },
                {
                    "type": "send_except",
                    "actor": "trigger_actor",
                    "text": "{{ actor }} pulls the lever.",
                },
            ],
        )

    def test_send_actions_reject_unsupported_fields_and_actor_refs(self):
        for action in (
            {
                "type": "send",
                "actor": "someone_else",
                "text": "You pull the lever.",
            },
            {
                "type": "send_except",
                "actor": "trigger_actor",
                "room": "trigger_room",
                "text": "{{ actor }} pulls the lever.",
            },
        ):
            with self.subTest(action_type=action["type"]):
                with self.assertRaises(TriggerStepSpecError):
                    normalize_trigger_steps([
                        {
                            "after_seconds": 0,
                            "actions": [action],
                        },
                    ])

        for text in (None, "   ", "x" * 4_001):
            with self.subTest(text=text):
                with self.assertRaises(TriggerStepSpecError):
                    normalize_trigger_steps([
                        {
                            "after_seconds": 0,
                            "actions": [
                                {
                                    "type": "send",
                                    "actor": "trigger_actor",
                                    "text": text,
                                },
                            ],
                        },
                    ])

    def test_send_and_send_except_deliver_distinct_actor_perspectives(self):
        observer = self.create_player("Observer", room=self.room)
        other_room = self.room.create_at("north")
        other_room_observer = self.create_player(
            "Other Room Observer",
            room=other_room,
        )
        other_runtime = self.world.create_spawn_world(
            instance_ref="send-step-other",
        )
        other_runtime_observer = self.create_player(
            "Other Runtime Observer",
            world=other_runtime,
            room=self.room,
        )
        for player in (
            observer,
            other_room_observer,
            other_runtime_observer,
        ):
            player.in_game = True
            player.save(update_fields=["in_game"])
        self._create_trigger(
            match="pull perspective lever",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "send",
                            "actor": "trigger_actor",
                            "text": "You pull the lever.",
                        },
                        {
                            "type": "send_except",
                            "actor": "trigger_actor",
                            "text": "{{ actor }} pulls the lever.",
                        },
                    ],
                },
            ],
        )

        from spawns import trigger_steps as trigger_step_runtime

        with patch(
            "spawns.trigger_steps._room_recipient_keys_for",
            wraps=trigger_step_runtime._room_recipient_keys_for,
        ) as recipient_query:
            with capture_game_messages() as messages:
                self._dispatch(
                    self.player.id,
                    "pull perspective lever",
                )

        recipient_query.assert_called_once_with(
            runtime_world_id=self.spawn_world.id,
            room_id=self.room.id,
        )

        actor_send = [
            entry
            for entry in messages
            if entry["player_key"] == self.player.key
            and entry["message"]["type"] == "notification./send"
        ]
        self.assertEqual(
            [entry["message"]["text"] for entry in actor_send],
            ["You pull the lever."],
        )
        self.assertFalse(any(
            entry["player_key"] == self.player.key
            and entry["message"]["type"] == "notification./sendexcept"
            for entry in messages
        ))

        observer_send_except = [
            entry
            for entry in messages
            if entry["player_key"] == observer.key
            and entry["message"]["type"] == "notification./sendexcept"
        ]
        self.assertEqual(
            [entry["message"]["text"] for entry in observer_send_except],
            ["Joe pulls the lever."],
        )
        self.assertFalse(any(
            entry["player_key"] == observer.key
            and entry["message"]["type"] == "notification./send"
            for entry in messages
        ))
        self.assertFalse(any(
            entry["player_key"]
            in {
                other_room_observer.key,
                other_runtime_observer.key,
            }
            and entry["message"]["type"] == "notification./sendexcept"
            for entry in messages
        ))

    def test_send_template_state_is_isolated_between_actions(self):
        self._create_trigger(
            match="test message isolation",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "send",
                            "actor": "trigger_actor",
                            "text": (
                                "{{ actor_state.update({'leaked': true}) "
                                "or 'First message.' }}"
                            ),
                        },
                        {
                            "type": "send",
                            "actor": "trigger_actor",
                            "text": (
                                "{{ actor_state.get("
                                "'leaked', 'State is isolated.') }}"
                            ),
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "test message isolation")

        actor_messages = [
            entry["message"]["text"]
            for entry in messages
            if entry["player_key"] == self.player.key
            and entry["message"]["type"] == "notification./send"
        ]
        self.assertEqual(
            actor_messages,
            ["First message.", "State is isolated."],
        )

    def test_send_actions_revalidate_rendered_text(self):
        cases = (
            ("{% set ignored = actor %}", "invalid_send_text"),
            ("{{ actor * 2000 }}", "send_text_too_long"),
        )

        for index, (text, expected_code) in enumerate(cases):
            with self.subTest(expected_code=expected_code):
                trigger = self._create_trigger(
                    match=f"test rendered message {index}",
                    conditions="",
                    steps=[
                        {
                            "after_seconds": 0,
                            "actions": [
                                {
                                    "type": "send",
                                    "actor": "trigger_actor",
                                    "text": text,
                                },
                            ],
                        },
                    ],
                )

                with capture_game_messages() as messages:
                    result = start_trigger_steps(
                        trigger=trigger,
                        actor=self.player,
                        room=self.room,
                    )

                self.assertFalse(result.started)
                self.assertEqual(result.code, expected_code)
                self.assertFalse(any(
                    entry["message"]["type"] == "notification./send"
                    for entry in messages
                ))

    def test_send_except_follows_actor_room_after_same_step_transfer(self):
        destination = self.room.create_at("east")
        origin_observer = self.create_player(
            "Origin Observer",
            room=self.room,
        )
        destination_observer = self.create_player(
            "Destination Observer",
            room=destination,
        )
        for player in (origin_observer, destination_observer):
            player.in_game = True
            player.save(update_fields=["in_game"])
        self._create_trigger(
            match="cross perspective threshold",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": (
                                f"/transfer self "
                                f"room@{destination.x},"
                                f"{destination.y},"
                                f"{destination.z}"
                            ),
                        },
                        {
                            "type": "send_except",
                            "actor": "trigger_actor",
                            "text": "{{ actor }} crosses the threshold.",
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            self._dispatch(
                self.player.id,
                "cross perspective threshold",
            )

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        self.assertTrue(any(
            entry["player_key"] == destination_observer.key
            and entry["message"]["type"] == "notification./sendexcept"
            and entry["message"]["text"] == "Joe crosses the threshold."
            for entry in messages
        ))
        self.assertFalse(any(
            entry["player_key"] == origin_observer.key
            and entry["message"]["type"] == "notification./sendexcept"
            for entry in messages
        ))

    def test_send_actions_require_connected_player_trigger_actor(self):
        trigger = self._create_trigger(
            match="mob perspective",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "send",
                            "actor": "trigger_actor",
                            "text": "You move.",
                        },
                    ],
                },
            ],
        )
        mob = self.create_mob("A restless statue")

        result = start_trigger_steps(
            trigger=trigger,
            actor=mob,
            room=self.room,
        )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "invalid_actor")
        self.assertFalse(
            ScheduledTriggerRun.objects.filter(trigger=trigger).exists()
        )

        self.player.in_game = False
        self.player.save(update_fields=["in_game"])
        disconnected = start_trigger_steps(
            trigger=trigger,
            actor=self.player,
            room=self.room,
        )
        self.assertFalse(disconnected.started)
        self.assertEqual(disconnected.code, "actor_unavailable")
        self.assertFalse(
            ScheduledTriggerRun.objects.filter(trigger=trigger).exists()
        )

    def test_delayed_send_except_follows_player_current_room(self):
        destination = self.room.create_at("west")
        origin_observer = self.create_player(
            "Origin Observer",
            room=self.room,
        )
        destination_observer = self.create_player(
            "Destination Observer",
            room=destination,
        )
        for player in (origin_observer, destination_observer):
            player.in_game = True
            player.save(update_fields=["in_game"])
        trigger = self._create_trigger(
            match="delayed perspective",
            conditions="",
            steps=[
                {
                    "after_seconds": 2,
                    "actions": [
                        {
                            "type": "send_except",
                            "actor": "trigger_actor",
                            "text": "{{ actor }} finishes the ritual.",
                        },
                    ],
                },
            ],
        )
        with self.captureOnCommitCallbacks(execute=True):
            start = start_trigger_steps(
                trigger=trigger,
                actor=self.player,
                room=self.room,
            )
        run = ScheduledTriggerRun.objects.get(pk=start.run_id)
        self.player.room = destination
        self.player.save(update_fields=["room"])

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = process_due_trigger_runs(now=run.next_run_ts)

        self.assertEqual(result["completed"], 1)
        self.assertTrue(any(
            entry["player_key"] == destination_observer.key
            and entry["message"]["type"] == "notification./sendexcept"
            and entry["message"]["text"] == "Joe finishes the ritual."
            for entry in messages
        ))
        self.assertFalse(any(
            entry["player_key"] == origin_observer.key
            and entry["message"]["type"] == "notification./sendexcept"
            for entry in messages
        ))

    def test_send_events_and_prior_mutation_roll_back_on_later_failure(self):
        observer = self.create_player("Observer", room=self.room)
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        seed_item = self.seed.spawn(self.player, self.spawn_world)
        trigger = self._create_trigger(
            match="broken perspective",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "consume_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.barley-seed",
                        },
                        {
                            "type": "send",
                            "actor": "trigger_actor",
                            "text": "You consume the seed.",
                        },
                        {
                            "type": "send_except",
                            "actor": "trigger_actor",
                            "text": "{{ actor }} consumes the seed.",
                        },
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "north",
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            result = start_trigger_steps(
                trigger=trigger,
                actor=self.player,
                room=self.room,
            )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "command_not_step_safe")
        self.assertTrue(Item.objects.filter(pk=seed_item.id).exists())
        self.assertFalse(any(
            entry["message"]["type"]
            in {
                "notification./send",
                "notification./sendexcept",
            }
            for entry in messages
        ))
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_positive_first_step_delay_is_normalized(self):
        normalized = normalize_trigger_steps([
            {
                "after_seconds": 2,
                "actions": [
                    {
                        "type": "echo",
                        "room": "trigger_room",
                        "text": "Charon considers the offer.",
                    },
                ],
            },
        ])

        self.assertEqual(normalized[0]["after_seconds"], 2)

    def test_zero_delay_is_allowed_between_distinct_steps(self):
        normalized = normalize_trigger_steps([
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "echo",
                        "room": "trigger_room",
                        "text": "Charon grunts.",
                    },
                ],
            },
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "echo",
                        "room": "trigger_room",
                        "text": "Charon gestures toward the ferry.",
                    },
                ],
            },
        ])

        self.assertEqual(
            [step["after_seconds"] for step in normalized],
            [0, 0],
        )

    def test_zero_delay_continuations_keep_one_transaction_per_step(self):
        trigger = self._create_trigger(
            match="board immediately",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "Charon grunts.",
                        },
                    ],
                },
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "Charon gestures toward the ferry.",
                        },
                    ],
                },
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The ferry departs.",
                        },
                    ],
                },
            ],
        )

        with patch(
            "spawns.tasks.continue_scheduled_trigger_run.apply_async"
        ) as schedule:
            with capture_game_messages() as start_messages:
                with self.captureOnCommitCallbacks(execute=True):
                    start = start_trigger_steps(
                        trigger=trigger,
                        actor=self.player,
                        room=self.room,
                    )
                    schedule.assert_not_called()

            run = ScheduledTriggerRun.objects.get(pk=start.run_id)
            self.assertEqual(run.next_step_index, 1)
            self.assertEqual(run.status, ScheduledTriggerRun.STATUS_ACTIVE)
            self.assertEqual(
                [
                    entry["message"]["text"]
                    for entry in start_messages
                    if entry["message"]["type"] == "notification./echo"
                ],
                ["Charon grunts."],
            )
            schedule.assert_called_once_with(
                kwargs={
                    "run_id": run.id,
                    "expected_step_index": 1,
                },
            )

            schedule.reset_mock()
            with capture_game_messages() as middle_messages:
                with self.captureOnCommitCallbacks(execute=True):
                    status = advance_due_trigger_run(
                        run_id=run.id,
                        expected_step_index=1,
                        now=run.started_ts,
                    )
                    schedule.assert_not_called()

            self.assertEqual(status, ScheduledTriggerRun.STATUS_ACTIVE)
            run.refresh_from_db()
            self.assertEqual(run.next_step_index, 2)
            self.assertEqual(
                [
                    entry["message"]["text"]
                    for entry in middle_messages
                    if entry["message"]["type"] == "notification./echo"
                ],
                ["Charon gestures toward the ferry."],
            )
            schedule.assert_called_once_with(
                kwargs={
                    "run_id": run.id,
                    "expected_step_index": 2,
                },
            )

            schedule.reset_mock()
            with capture_game_messages() as duplicate_messages:
                with self.captureOnCommitCallbacks(execute=True):
                    duplicate_status = advance_due_trigger_run(
                        run_id=run.id,
                        expected_step_index=1,
                        now=run.started_ts,
                    )

            self.assertIsNone(duplicate_status)
            self.assertEqual(duplicate_messages, [])
            schedule.assert_not_called()

            with capture_game_messages() as final_messages:
                with self.captureOnCommitCallbacks(execute=True):
                    final_status = advance_due_trigger_run(
                        run_id=run.id,
                        expected_step_index=2,
                        now=run.started_ts,
                    )

            self.assertEqual(
                final_status,
                ScheduledTriggerRun.STATUS_COMPLETED,
            )
            run.refresh_from_db()
            self.assertEqual(
                run.status,
                ScheduledTriggerRun.STATUS_COMPLETED,
            )
            self.assertEqual(
                [
                    entry["message"]["text"]
                    for entry in final_messages
                    if entry["message"]["type"] == "notification./echo"
                ],
                ["The ferry departs."],
            )
            schedule.assert_not_called()

    def test_periodic_worker_recovers_a_lost_zero_delay_continuation(self):
        trigger = self._create_trigger(
            match="recover immediate continuation",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The first bell rings.",
                        },
                    ],
                },
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The second bell rings.",
                        },
                    ],
                },
            ],
        )
        with patch(
            "spawns.tasks.continue_scheduled_trigger_run.apply_async"
        ):
            with self.captureOnCommitCallbacks(execute=True):
                start = start_trigger_steps(
                    trigger=trigger,
                    actor=self.player,
                    room=self.room,
                )
        run = ScheduledTriggerRun.objects.get(pk=start.run_id)

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = process_due_trigger_runs(
                    limit=1,
                    now=run.started_ts,
                )

        self.assertEqual(
            result,
            {"processed": 1, "completed": 1, "cancelled": 0},
        )
        self.assertEqual(
            [
                entry["message"]["text"]
                for entry in messages
                if entry["message"]["type"] == "notification./echo"
            ],
            ["The second bell rings."],
        )

    def test_delayed_first_step_is_committed_before_authored_actions_run(self):
        trigger = self._create_trigger(
            match="wait for charon",
            conditions="",
            steps=[
                {
                    "after_seconds": 2,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "Charon considers the offer.",
                        },
                    ],
                },
            ],
        )
        request_id = uuid.uuid4()

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = start_trigger_steps(
                    trigger=trigger,
                    actor=self.player,
                    room=self.room,
                    request_id=request_id,
                    request_segment="r.2",
                    request_connection_id="connection.original",
                )

        self.assertTrue(result.started)
        run = ScheduledTriggerRun.objects.get(pk=result.run_id)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_ACTIVE)
        self.assertEqual(run.next_step_index, 0)
        self.assertEqual(
            run.next_run_ts,
            run.started_ts + timedelta(seconds=2),
        )
        self.assertEqual(run.request_id, request_id)
        self.assertEqual(run.request_segment, "r.2")
        self.assertEqual(
            run.request_connection_id,
            "connection.original",
        )
        self.assertFalse(any(
            entry["message"]["type"] == "notification./echo"
            for entry in messages
        ))
        accepted = [
            entry
            for entry in messages
            if entry["message"]["type"] == "cmd.trigger.accepted"
        ]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["player_key"], self.player.key)
        self.assertEqual(
            accepted[0]["connection_id"],
            "connection.original",
        )
        self.assertEqual(
            accepted[0]["message"]["data"],
            {
                "_event_id": accepted[0]["message"]["data"]["_event_id"],
                "request_id": str(request_id),
                "request_segment": "r.2",
                "status": "accepted",
                "delayed": True,
                "first_step_delay_seconds": 2,
                "first_step_due_at": run.next_run_ts.isoformat(),
            },
        )

        self.assertEqual(
            process_due_trigger_runs(
                now=run.next_run_ts - timedelta(microseconds=1),
            )["processed"],
            0,
        )
        with capture_game_messages() as due_messages:
            with self.captureOnCommitCallbacks(execute=True):
                due_result = process_due_trigger_runs(
                    now=run.next_run_ts,
                )

        self.assertEqual(due_result["completed"], 1)
        run.refresh_from_db()
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        self.assertTrue(any(
            entry["message"]["type"] == "notification./echo"
            and entry["message"].get("text")
            == "Charon considers the offer."
            for entry in due_messages
        ))
        self.assertEqual(
            [
                entry["message"]["type"]
                for entry in due_messages
                if entry["player_key"] == self.player.key
            ],
            [
                "notification./echo",
                "cmd.trigger.completed",
            ],
        )
        completed = due_messages[-1]
        self.assertEqual(
            completed["connection_id"],
            "connection.original",
        )
        self.assertEqual(
            {
                key: value
                for key, value in completed["message"]["data"].items()
                if key != "_event_id"
            },
            {
                "request_id": str(request_id),
                "request_segment": "r.2",
                "status": "completed",
                "receipt_status": "completed",
            },
        )

    def test_multiple_matching_runs_have_one_correlated_lifecycle_owner(self):
        owner = self._create_trigger(
            match="wake ferrymen",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The first ferryman answers.",
                        },
                    ],
                },
            ],
        )
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        failing = self._create_trigger(
            match="wake ferrymen",
            conditions="",
            steps=[
                {
                    "after_seconds": 2,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": obol.code,
                            "amount": 1,
                        },
                    ],
                },
            ],
        )
        request_id = uuid.uuid4()

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = execute_command_fallback_trigger(
                    actor=self.player,
                    text="wake ferrymen",
                    connection_id="connection.original",
                    request_id=request_id,
                    request_segment="r",
                )

        self.assertTrue(result.handled)
        owner_run = ScheduledTriggerRun.objects.get(trigger=owner)
        failing_run = ScheduledTriggerRun.objects.get(trigger=failing)
        self.assertEqual(
            owner_run.status,
            ScheduledTriggerRun.STATUS_COMPLETED,
        )
        self.assertEqual(owner_run.request_id, request_id)
        self.assertEqual(
            owner_run.request_connection_id,
            "connection.original",
        )
        self.assertEqual(
            failing_run.status,
            ScheduledTriggerRun.STATUS_ACTIVE,
        )
        self.assertEqual(failing_run.request_id, request_id)
        self.assertIsNone(failing_run.request_connection_id)
        self.assertEqual(
            [
                entry["message"]["type"]
                for entry in messages
                if entry["player_key"] == self.player.key
            ],
            [
                "cmd.trigger.accepted",
                "notification./echo",
                "cmd.trigger.completed",
            ],
        )

        with capture_game_messages() as failure_messages:
            with self.captureOnCommitCallbacks(execute=True):
                due_result = process_due_trigger_runs(
                    now=failing_run.next_run_ts,
                )

        self.assertEqual(due_result["cancelled"], 1)
        self.assertFalse(any(
            entry["message"]["type"].startswith("cmd.trigger.")
            for entry in failure_messages
        ))
        self.assertEqual(len(failure_messages), 1)
        self.assertEqual(
            failure_messages[0]["message"]["type"],
            "notification.trigger.cancelled",
        )
        self.assertEqual(
            failure_messages[0]["message"]["text"],
            "That action can no longer be completed.",
        )
        self.assertIsNone(failure_messages[0]["connection_id"])

    def test_later_step_cancellation_is_private_safe_and_correlated(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 9},
            reason="test.setup",
            emit_event=False,
        )
        trigger = self._create_trigger(
            match="attempt delayed fare",
            conditions="",
            steps=[
                {
                    "after_seconds": 2,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )
        request_id = uuid.uuid4()
        with self.captureOnCommitCallbacks(execute=True):
            start = start_trigger_steps(
                trigger=trigger,
                actor=self.player,
                room=self.room,
                request_id=request_id,
                request_segment="r.3",
                request_connection_id="connection.original",
            )
        run = ScheduledTriggerRun.objects.get(pk=start.run_id)

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = process_due_trigger_runs(now=run.next_run_ts)

        self.assertEqual(result["cancelled"], 1)
        run.refresh_from_db()
        self.assertEqual(run.failure_code, "insufficient_funds")
        cancellation = [
            entry
            for entry in messages
            if entry["message"]["type"] == "cmd.trigger.cancelled"
        ]
        self.assertEqual(len(cancellation), 1)
        self.assertEqual(cancellation[0]["player_key"], self.player.key)
        self.assertEqual(
            cancellation[0]["connection_id"],
            "connection.original",
        )
        message = cancellation[0]["message"]
        self.assertNotIn("text", message)
        self.assertEqual(
            {
                key: value
                for key, value in message["data"].items()
                if key != "_event_id"
            },
            {
                "request_id": str(request_id),
                "request_segment": "r.3",
                "status": "cancelled",
                "code": "trigger_cancelled",
                "message": "That action can no longer be completed.",
                "receipt_status": "completed",
            },
        )
        self.assertNotIn("insufficient", str(message))
        safe_notifications = [
            entry
            for entry in messages
            if entry["message"]["type"]
            == "notification.trigger.cancelled"
        ]
        self.assertEqual(len(safe_notifications), 1)
        self.assertIsNone(safe_notifications[0]["connection_id"])
        self.assertEqual(
            safe_notifications[0]["message"]["text"],
            "That action can no longer be completed.",
        )

    def test_unhandled_later_step_failure_marks_receipt_failed(self):
        trigger = self._create_trigger(
            match="encounter internal failure",
            conditions="",
            steps=[
                {
                    "after_seconds": 2,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "This never becomes visible.",
                        },
                    ],
                },
            ],
        )
        request_id = uuid.uuid4()
        with self.captureOnCommitCallbacks(execute=True):
            start = start_trigger_steps(
                trigger=trigger,
                actor=self.player,
                room=self.room,
                request_id=request_id,
                request_segment="r.8",
                request_connection_id="connection.original",
            )
        run = ScheduledTriggerRun.objects.get(pk=start.run_id)
        private_detail = "private trigger exception detail"

        with patch(
            "spawns.trigger_steps._execute_current_step",
            side_effect=RuntimeError(private_detail),
        ), capture_game_messages() as messages:
            with self.assertLogs("spawns.trigger_steps", level="ERROR"):
                with self.captureOnCommitCallbacks(execute=True):
                    result = process_due_trigger_runs(
                        now=run.next_run_ts,
                    )

        self.assertEqual(result["cancelled"], 1)
        run.refresh_from_db()
        self.assertEqual(run.failure_code, "step_exception")
        cancellation = next(
            entry["message"]
            for entry in messages
            if entry["message"]["type"] == "cmd.trigger.cancelled"
        )
        self.assertEqual(
            {
                key: value
                for key, value in cancellation["data"].items()
                if key != "_event_id"
            },
            {
                "request_id": str(request_id),
                "request_segment": "r.8",
                "status": "cancelled",
                "code": "trigger_cancelled",
                "message": "That action can no longer be completed.",
                "receipt_status": "failed",
            },
        )
        self.assertNotIn(private_detail, str(cancellation))

    def test_uncorrelated_delayed_failure_emits_no_lifecycle_message(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        trigger = self._create_trigger(
            match="ambient delayed fare",
            conditions="",
            steps=[
                {
                    "after_seconds": 2,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": obol.code,
                            "amount": 1,
                        },
                    ],
                },
            ],
        )
        start = start_trigger_steps(
            trigger=trigger,
            actor=self.player,
            room=self.room,
        )
        run = ScheduledTriggerRun.objects.get(pk=start.run_id)

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = process_due_trigger_runs(now=run.next_run_ts)

        self.assertEqual(result["cancelled"], 1)
        self.assertEqual(messages, [])

    def test_failed_step_condition_acknowledges_processed_command(self):
        trigger = self._create_trigger(
            match="pay without funds",
            conditions=json.dumps({
                "gte": ["actor.balances.obol", 10],
            }),
            steps=[
                {
                    "after_seconds": 2,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "This never happens.",
                        },
                    ],
                },
            ],
        )
        trigger.show_details_on_failure = True
        trigger.failure_message = "Charon requires 10 obols."
        trigger.save(
            update_fields=[
                "show_details_on_failure",
                "failure_message",
            ]
        )
        request_id = uuid.uuid4()

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                connection_id="connection.original",
                payload={
                    "text": "pay without funds",
                    "_request_id": str(request_id),
                    "_request_segment": "r.4",
                },
            )

        self.assertFalse(
            ScheduledTriggerRun.objects.filter(trigger=trigger).exists()
        )
        self.assertEqual(len(messages), 1)
        feedback = messages[0]
        self.assertEqual(
            feedback["message"]["type"],
            "cmd.trigger.rejected",
        )
        self.assertEqual(
            feedback["connection_id"],
            "connection.original",
        )
        self.assertEqual(
            feedback["message"]["text"],
            "Charon requires 10 obols.",
        )
        self.assertEqual(
            feedback["message"]["data"],
            {
                "request_id": str(request_id),
                "request_segment": "r.4",
                "status": "rejected",
                "code": "trigger_rejected",
                "reason_code": "conditions_failed",
                "receipt_status": "completed",
                "message": "Charon requires 10 obols.",
            },
        )

    def test_handled_failure_reason_overrides_earlier_condition_refusal(self):
        refused = self._create_trigger(
            match="mixed trigger outcome",
            conditions=json.dumps({
                "gte": ["actor.balances.obol", 10],
            }),
            steps=[
                {
                    "after_seconds": 2,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "This condition prevents this.",
                        },
                    ],
                },
            ],
        )
        refused.show_details_on_failure = True
        refused.failure_message = "The authored condition declines."
        refused.save(
            update_fields=[
                "show_details_on_failure",
                "failure_message",
            ]
        )
        self._create_trigger(
            match="mixed trigger outcome",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "not_a_real_trigger_action",
                        },
                    ],
                },
            ],
        )
        request_id = uuid.uuid4()

        with capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                connection_id="connection.original",
                payload={
                    "text": "mixed trigger outcome",
                    "_request_id": str(request_id),
                },
            )

        rejection = next(
            entry["message"]
            for entry in messages
            if entry["message"]["type"] == "cmd.trigger.rejected"
        )
        self.assertEqual(rejection["data"]["request_id"], str(request_id))
        self.assertEqual(rejection["data"]["reason_code"], "invalid_steps")
        self.assertEqual(
            rejection["data"]["receipt_status"],
            "completed",
        )
        self.assertIn("Error:", rejection["text"])

    def test_command_dispatch_defers_receipt_to_trigger_lifecycle(self):
        self._create_trigger(
            match="wait for lifecycle",
            conditions="",
            steps=[
                {
                    "after_seconds": 2,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The delayed response arrives.",
                        },
                    ],
                },
            ],
        )
        request_id = uuid.uuid4()

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                dispatch_command(
                    command_type="text",
                    player_id=self.player.id,
                    connection_id="connection.original",
                    payload={
                        "text": "wait for lifecycle",
                        "_request_id": str(request_id),
                    },
                )

        self.assertEqual(
            [
                entry["message"]["type"]
                for entry in messages
                if entry["player_key"] == self.player.key
            ],
            ["cmd.trigger.accepted"],
        )

    def test_legacy_success_is_not_rejected_by_later_gated_step_trigger(self):
        successful = self._create_trigger(
            match="wake mixed ferrymen",
            conditions="",
            steps=[],
        )
        successful.script = (
            "/cmd room -- /echo -- The first ferryman answers."
        )
        successful.save(update_fields=["script"])
        self._create_trigger(
            match="wake mixed ferrymen",
            conditions="",
            steps=[
                {
                    "after_seconds": 2,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The second ferryman answers.",
                        },
                    ],
                },
            ],
        )
        request_id = uuid.uuid4()

        with patch(
            "spawns.trigger_steps.start_trigger_steps",
            return_value=TriggerStepStartResult(
                started=False,
                feedback="More time is needed.",
                code="gated",
            ),
        ), capture_game_messages() as messages:
            dispatch_command(
                command_type="text",
                player_id=self.player.id,
                connection_id="connection.original",
                payload={
                    "text": "wake mixed ferrymen",
                    "_request_id": str(request_id),
                    "_request_segment": "r",
                },
            )

        self.assertTrue(any(
            entry["message"].get("text")
            == "The first ferryman answers."
            for entry in messages
        ))
        self.assertFalse(any(
            entry["message"]["type"] == "cmd.trigger.rejected"
            for entry in messages
        ))
        self.assertTrue(any(
            entry["message"]["type"] == "cmd.text.trigger"
            for entry in messages
        ))

    def test_debit_currency_action_normalizes_explicit_player_charge(self):
        normalized = normalize_trigger_steps([
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "debit_currency",
                        "actor": "trigger_actor",
                        "currency": "ObOl",
                        "amount": 10,
                    },
                ],
            },
        ])

        self.assertEqual(
            normalized[0]["actions"][0],
            {
                "type": "debit_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": 10,
            },
        )

    def test_grant_currency_action_normalizes_explicit_player_award(self):
        normalized = normalize_trigger_steps([
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "grant_currency",
                        "actor": "trigger_actor",
                        "currency": "ObOl",
                        "amount": MAX_CURRENCY_AMOUNT,
                    },
                ],
            },
        ])

        self.assertEqual(
            normalized[0]["actions"][0],
            {
                "type": "grant_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": MAX_CURRENCY_AMOUNT,
            },
        )

    def test_command_action_normalizes_room_actor_and_mob_subjects(self):
        normalized = normalize_trigger_steps([
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "command",
                        "subject": "trigger_actor",
                        "command": " say I have paid. ",
                    },
                    {
                        "type": "command",
                        "subject": "trigger_room",
                        "command": "/echo The ferry pulls away.",
                    },
                    {
                        "type": "command",
                        "subject": {
                            "type": "mob",
                            "room": "trigger_room",
                            "mob": "mobdefinition.charon",
                            "where": {
                                "eq": ["state.character.on_duty", True],
                            },
                        },
                        "command": "emote grunts satisfactorily.",
                    },
                ],
            },
        ])

        self.assertEqual(
            normalized[0]["actions"],
            [
                {
                    "type": "command",
                    "subject": "trigger_actor",
                    "command": "say I have paid.",
                },
                {
                    "type": "command",
                    "subject": "trigger_room",
                    "command": "/echo The ferry pulls away.",
                },
                {
                    "type": "command",
                    "subject": {
                        "type": "mob",
                        "room": "trigger_room",
                        "mob": "mobdefinition.charon",
                        "where": {
                            "eq": ["state.character.on_duty", True],
                        },
                    },
                    "command": "emote grunts satisfactorily.",
                },
            ],
        )

    def test_command_action_rejects_unsafe_or_invalid_shapes(self):
        invalid_actions = [
            {
                "type": "command",
                "subject": "another_player",
                "command": "say no",
            },
            {
                "type": "command",
                "subject": {
                    "type": "player",
                    "room": "trigger_room",
                    "mob": "mobdefinition.charon",
                },
                "command": "say no",
            },
            {
                "type": "command",
                "subject": {
                    "type": "mob",
                    "room": "another_room",
                    "mob": "mobdefinition.charon",
                },
                "command": "say no",
            },
            {
                "type": "command",
                "subject": "trigger_actor",
                "command": "",
            },
            {
                "type": "command",
                "subject": "trigger_actor",
                "command": "say one\nsay two",
            },
            {
                "type": "command",
                "subject": "trigger_actor",
                "command": "say one; say two",
            },
            {
                "type": "command",
                "subject": "trigger_actor",
                "command": "say one && say two",
            },
            {
                "type": "command",
                "subject": "trigger_actor",
                "command": "!1",
            },
            {
                "type": "command",
                "subject": "trigger_actor",
                "command": "/cmd room -- /echo no",
            },
            {
                "type": "command",
                "subject": "trigger_actor",
                "command": "say no",
                "issuer": "trigger_room",
            },
        ]

        for action in invalid_actions:
            with self.subTest(action=action):
                with self.assertRaises(TriggerStepSpecError):
                    normalize_trigger_steps([
                        {
                            "after_seconds": 0,
                            "actions": [action],
                        },
                    ])

    def test_command_action_can_follow_currency_debit(self):
        normalized = normalize_trigger_steps([
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "debit_currency",
                        "actor": "trigger_actor",
                        "currency": "obol",
                        "amount": 10,
                    },
                    {
                        "type": "command",
                        "subject": "trigger_actor",
                        "command": "say The fare is paid.",
                    },
                ],
            },
        ])

        self.assertEqual(
            normalized[0]["actions"][1]["command"],
            "say The fare is paid.",
        )

    def test_command_echo_and_currency_actions_can_interleave_in_authored_order(self):
        actions = [
            {
                "type": "command",
                "subject": "trigger_actor",
                "command": "say The fare is due.",
            },
            {
                "type": "debit_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": 10,
            },
            {
                "type": "echo",
                "room": "trigger_room",
                "text": "The ferryman accepts the fare.",
            },
            {
                "type": "grant_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": 2,
            },
            {
                "type": "command",
                "subject": "trigger_actor",
                "command": "say Get on board.",
            },
            {
                "type": "debit_currency",
                "actor": "trigger_actor",
                "currency": "drachma",
                "amount": 1,
            },
        ]

        normalized = normalize_trigger_steps([
            {
                "after_seconds": 0,
                "actions": actions,
            },
        ])

        self.assertEqual(normalized[0]["actions"], actions)

    def test_step_actions_require_item_and_mob_mutation_prefix(self):
        invalid_action_lists = [
            [
                {
                    "type": "command",
                    "subject": "trigger_actor",
                    "command": "say Too soon.",
                },
                {
                    "type": "grant_item",
                    "actor": "trigger_actor",
                    "item": "itemdefinition.harvested-barley",
                },
            ],
            [
                {
                    "type": "debit_currency",
                    "actor": "trigger_actor",
                    "currency": "obol",
                    "amount": 10,
                },
                {
                    "type": "grant_item",
                    "actor": "trigger_actor",
                    "item": "itemdefinition.harvested-barley",
                },
            ],
            [
                {
                    "type": "grant_currency",
                    "actor": "trigger_actor",
                    "currency": "obol",
                    "amount": 10,
                },
                {
                    "type": "grant_item",
                    "actor": "trigger_actor",
                    "item": "itemdefinition.harvested-barley",
                },
            ],
            [
                {
                    "type": "echo",
                    "room": "trigger_room",
                    "text": "Too soon.",
                },
                {
                    "type": "set_mob",
                    "room": "trigger_room",
                    "mob": "mobdefinition.ferryman",
                    "fields": {
                        "description": "The ferryman waits.",
                    },
                },
            ],
        ]

        for actions in invalid_action_lists:
            with self.subTest(actions=actions):
                with self.assertRaisesMessage(
                    TriggerStepSpecError,
                    "item and mob mutations must precede all currency, command, "
                    "echo, send, and send_except actions",
                ):
                    normalize_trigger_steps([
                        {
                            "after_seconds": 0,
                            "actions": actions,
                        },
                    ])

    def test_debit_currency_action_rejects_invalid_fields_and_amounts(self):
        invalid_actions = [
            {
                "type": "debit_currency",
                "actor": "other_actor",
                "currency": "obol",
                "amount": 10,
            },
            {
                "type": "debit_currency",
                "actor": "trigger_actor",
                "currency": "",
                "amount": 10,
            },
            {
                "type": "debit_currency",
                "actor": "trigger_actor",
                "currency": "currency.obol",
                "amount": 10,
            },
            {
                "type": "debit_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": 0,
            },
            {
                "type": "debit_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": True,
            },
            {
                "type": "debit_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": 1.5,
            },
            {
                "type": "debit_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": 10,
                "message": "unsupported",
            },
        ]

        for action in invalid_actions:
            with self.subTest(action=action):
                with self.assertRaises(TriggerStepSpecError):
                    normalize_trigger_steps([
                        {
                            "after_seconds": 0,
                            "actions": [action],
                        },
                    ])

        with self.assertRaisesMessage(
            TriggerStepSpecError,
            "item and mob mutations must precede all currency, command, echo, "
            "send, and send_except actions",
        ):
            normalize_trigger_steps([
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                        {
                            "type": "grant_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.harvested-barley",
                        },
                    ],
                },
            ])

    def test_grant_currency_action_rejects_invalid_fields_and_amounts(self):
        invalid_actions = [
            {
                "type": "grant_currency",
                "actor": "other_actor",
                "currency": "obol",
                "amount": 10,
            },
            {
                "type": "grant_currency",
                "actor": "trigger_actor",
                "currency": "",
                "amount": 10,
            },
            {
                "type": "grant_currency",
                "actor": "trigger_actor",
                "currency": "currency.obol",
                "amount": 10,
            },
            {
                "type": "grant_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": 0,
            },
            {
                "type": "grant_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": True,
            },
            {
                "type": "grant_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": 1.5,
            },
            {
                "type": "grant_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": MAX_CURRENCY_AMOUNT + 1,
            },
            {
                "type": "grant_currency",
                "actor": "trigger_actor",
                "currency": "obol",
                "amount": 10,
                "message": "unsupported",
            },
        ]

        for action in invalid_actions:
            with self.subTest(action=action):
                with self.assertRaises(TriggerStepSpecError):
                    normalize_trigger_steps([
                        {
                            "after_seconds": 0,
                            "actions": [action],
                        },
                    ])

    def test_debit_item_prelocks_are_bounded_and_pin_candidate_ids(self):
        actor_items = [
            self.seed.spawn(self.player, self.spawn_world)
            for _index in range(5)
        ]
        room_items = [
            self.mature.spawn(self.room, self.spawn_world)
            for _index in range(5)
        ]
        run = SimpleNamespace(
            runtime_world_id=self.spawn_world.id,
            room_id=self.room.id,
            actor_type="player",
            actor_id=self.player.id,
        )
        actions = [
            {
                "type": "consume_item",
                "actor": "trigger_actor",
                "item_definition_id": self.seed.id,
                "count": 2,
            },
            {
                "type": "replace_room_item",
                "target": "crop",
                "with_item_definition_id": self.seedling.id,
            },
            {
                "type": "consume_room_item",
                "room": "trigger_room",
                "item_definition_id": self.mature.id,
                "count": 3,
            },
            {
                "type": "debit_currency",
                "actor": "trigger_actor",
                "currency_id": 1,
                "amount": 10,
            },
        ]

        prelocks = _prelock_step_resources(
            run=run,
            actions=actions,
            bindings={
                "crop": {
                    "type": "item",
                    "id": room_items[0].id,
                },
            },
        )

        self.assertEqual(
            prelocks.actor_item_ids_by_definition[self.seed.id],
            tuple(item.id for item in actor_items[:2]),
        )
        captured_room_ids = prelocks.room_item_ids_by_definition[self.mature.id]
        self.assertEqual(
            captured_room_ids,
            tuple(item.id for item in room_items[:4]),
        )

        Item.objects.filter(pk__in=captured_room_ids).delete()
        late_item = self.mature.spawn(self.room, self.spawn_world)
        with self.assertRaises(TriggerStepExecutionError) as raised:
            _consume_room_item(
                run=run,
                action=actions[2],
                definition=self.mature,
                candidate_ids=captured_room_ids,
            )

        self.assertEqual(raised.exception.code, "required_room_item_missing")
        self.assertTrue(Item.objects.filter(pk=late_item.id).exists())

    def test_debit_mob_prelocks_are_bounded_and_reject_oversized_predicates(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="bounded-toll-guards",
            name="a toll guard",
        )
        guards = Mob.objects.bulk_create([
            Mob(
                world=self.spawn_world,
                room=self.room,
                definition=definition,
                name=f"Toll Guard {index}",
            )
            for index in range(5)
        ])
        run = SimpleNamespace(
            runtime_world_id=self.spawn_world.id,
            room_id=self.room.id,
            actor_type="player",
            actor_id=self.player.id,
        )
        debit = {
            "type": "debit_currency",
            "actor": "trigger_actor",
            "currency_id": 1,
            "amount": 10,
        }
        set_mob = {
            "type": "set_mob",
            "room": "trigger_room",
            "mob_definition_id": definition.id,
            "fields": {"attackable": True},
        }

        prelocks = _prelock_step_resources(
            run=run,
            actions=[set_mob, debit],
            bindings={},
        )

        self.assertEqual(
            prelocks.mob_ids_by_definition[definition.id],
            tuple(guard.id for guard in guards[:2]),
        )

        Mob.objects.bulk_create([
            Mob(
                world=self.spawn_world,
                room=self.room,
                definition=definition,
                name=f"Extra Toll Guard {index}",
            )
            for index in range(
                MAX_TRIGGER_SET_MOB_CANDIDATES - len(guards) + 1
            )
        ])
        set_mob["where"] = {
            "eq": ["state.character.on_duty", True],
        }
        with self.assertRaises(TriggerStepExecutionError) as raised:
            _prelock_step_resources(
                run=run,
                actions=[set_mob, debit],
                bindings={},
            )

        self.assertEqual(raised.exception.code, "set_mob_candidate_limit")

    def test_command_mob_subject_prelocks_are_bounded(self):
        definition = MobDefinition.objects.create(
            world=self.world,
            slug="bounded-command-guards",
            name="a speaking guard",
        )
        guards = Mob.objects.bulk_create([
            Mob(
                world=self.spawn_world,
                room=self.room,
                definition=definition,
                name=f"Speaking Guard {index}",
            )
            for index in range(5)
        ])
        run = SimpleNamespace(
            runtime_world_id=self.spawn_world.id,
            room_id=self.room.id,
            actor_type="player",
            actor_id=self.player.id,
        )
        command = {
            "type": "command",
            "subject": {
                "type": "mob",
                "room": "trigger_room",
                "mob_definition_id": definition.id,
            },
            "command": "say Halt.",
        }

        prelocks = _prelock_step_resources(
            run=run,
            actions=[command],
            bindings={},
        )

        self.assertEqual(
            prelocks.mob_ids_by_definition[definition.id],
            tuple(guard.id for guard in guards[:2]),
        )

        Mob.objects.bulk_create([
            Mob(
                world=self.spawn_world,
                room=self.room,
                definition=definition,
                name=f"Extra Speaking Guard {index}",
            )
            for index in range(
                MAX_TRIGGER_SET_MOB_CANDIDATES - len(guards) + 1
            )
        ])
        command["subject"]["where"] = {
            "eq": ["state.character.on_duty", True],
        }
        with self.assertRaises(TriggerStepExecutionError) as raised:
            _prelock_step_resources(
                run=run,
                actions=[command],
                bindings={},
            )

        self.assertEqual(
            raised.exception.code,
            "command_subject_candidate_limit",
        )

    def test_debit_currency_charges_player_and_notifies_actor_and_room(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        mutate_balances(
            self.player,
            {obol: 20},
            reason="test.setup",
            emit_event=False,
        )
        observer = self.create_player(
            "Observer",
            user=self.create_user("currency-observer@example.com"),
        )
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        trigger = self._create_trigger(
            match="pay toll",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )
        revision_before = self.player.wallet_revision

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "pay toll")

        self.player.refresh_from_db()
        self.assertEqual(self.player.wallet_revision, revision_before + 1)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            10,
        )
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        self.assertEqual(
            run.steps[0]["actions"][0]["currency_id"],
            obol.id,
        )
        self.assertEqual(
            run.steps[0]["actions"][0]["currency"],
            "obol",
        )

        debit_messages = [
            entry
            for entry in messages
            if (
                entry["message"]["type"]
                == "notification.trigger.currency_debited"
            )
        ]
        self.assertEqual(
            [
                (entry["player_key"], entry["message"]["text"])
                for entry in debit_messages
            ],
            [
                (self.player.key, "You part with 10 obols."),
                (observer.key, "Joe parts with 10 obols."),
            ],
        )
        observer_data = debit_messages[1]["message"]["data"]
        self.assertEqual(
            observer_data["money"],
            {
                "amount": 10,
                "currency": "obol",
                "display": "10 Obols",
            },
        )
        self.assertNotIn("before", observer_data)
        self.assertNotIn("after", observer_data)
        self.assertNotIn("wallet_revision", observer_data)
        wallet_messages = [
            entry
            for entry in messages
            if entry["message"]["type"] == "currency.balances_changed"
        ]
        self.assertEqual(
            [entry["player_key"] for entry in wallet_messages],
            [self.player.key],
        )

    def test_grant_currency_awards_player_and_notifies_actor_and_room(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        self.assertFalse(
            PlayerCurrencyBalance.objects.filter(
                player=self.player,
                currency=obol,
            ).exists()
        )
        observer = self.create_player(
            "Observer",
            user=self.create_user("currency-grant-observer@example.com"),
        )
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        trigger = self._create_trigger(
            match="claim stipend",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "grant_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )
        revision_before = self.player.wallet_revision

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "claim stipend")

        self.player.refresh_from_db()
        self.assertEqual(self.player.wallet_revision, revision_before + 1)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            10,
        )
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        self.assertEqual(
            run.steps[0]["actions"][0]["currency_id"],
            obol.id,
        )
        self.assertEqual(
            run.steps[0]["actions"][0]["currency"],
            "obol",
        )

        grant_messages = [
            entry
            for entry in messages
            if (
                entry["message"]["type"]
                == "notification.trigger.currency_granted"
            )
        ]
        self.assertEqual(
            [
                (entry["player_key"], entry["message"]["text"])
                for entry in grant_messages
            ],
            [
                (self.player.key, "You receive 10 obols."),
                (observer.key, "Joe receives 10 obols."),
            ],
        )
        observer_data = grant_messages[1]["message"]["data"]
        self.assertEqual(
            observer_data["money"],
            {
                "amount": 10,
                "currency": "obol",
                "display": "10 Obols",
            },
        )
        self.assertNotIn("before", observer_data)
        self.assertNotIn("after", observer_data)
        self.assertNotIn("wallet_revision", observer_data)
        wallet_messages = [
            entry
            for entry in messages
            if entry["message"]["type"] == "currency.balances_changed"
        ]
        self.assertEqual(
            [entry["player_key"] for entry in wallet_messages],
            [self.player.key],
        )
        self.assertEqual(
            wallet_messages[0]["message"]["data"]["reason"],
            "trigger.grant_currency",
        )
        self.assertEqual(
            wallet_messages[0]["message"]["data"]["changes"][0]["delta"],
            10,
        )

    def test_sixth_step_can_force_trigger_player_to_say(self):
        original_observer = self.create_player(
            "Original Speech Observer",
            user=self.create_user("original-speech-observer@example.com"),
        )
        original_observer.in_game = True
        original_observer.save(update_fields=["in_game"])
        other_room = self.room.create_at("north")
        current_observer = self.create_player(
            "Current Speech Observer",
            user=self.create_user("current-speech-observer@example.com"),
        )
        current_observer.room = other_room
        current_observer.in_game = True
        current_observer.save(update_fields=["room", "in_game"])
        steps = [
            {
                "after_seconds": 0 if index == 0 else 1,
                "actions": [
                    {
                        "type": "echo",
                        "room": "trigger_room",
                        "text": f"The ferry bell marks stage {index + 1}.",
                    },
                ],
            }
            for index in range(5)
        ]
        steps.append({
            "after_seconds": 1,
            "actions": [
                {
                    "type": "command",
                    "subject": "trigger_actor",
                    "command": "say I accept the ferryman's price.",
                },
            ],
        })
        trigger = self._create_trigger(
            match="begin crossing oath",
            conditions="",
            steps=steps,
        )

        self._dispatch(self.player.id, "begin crossing oath")
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.next_step_index, 1)
        self.player.room = other_room
        self.player.save(update_fields=["room"])

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = process_due_trigger_runs(
                    limit=10,
                    now=run.started_ts + timedelta(seconds=5),
                )

        self.assertEqual(result["processed"], 5)
        run.refresh_from_db()
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        say_messages = [
            entry
            for entry in messages
            if entry["message"]["type"] in {
                "cmd.say.success",
                "notification.cmd.say.success",
            }
        ]
        self.assertEqual(
            {entry["player_key"] for entry in say_messages},
            {self.player.key, current_observer.key},
        )
        self.assertNotIn(
            original_observer.key,
            {entry["player_key"] for entry in say_messages},
        )
        self.assertTrue(all(
            entry["message"]["data"]["actor"]["key"] == self.player.key
            and entry["message"]["data"]["text"]
            == "I accept the ferryman's price."
            for entry in say_messages
        ))

    def test_room_command_executes_echo_through_step_outbox(self):
        observer = self.create_player(
            "Room Command Observer",
            user=self.create_user("room-command-observer@example.com"),
        )
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        trigger = self._create_trigger(
            match="rock ferry",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_room",
                            "command": "/echo The ferry rocks against the pier.",
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "rock ferry")

        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        room_echoes = [
            entry
            for entry in messages
            if entry["message"]["type"] == "notification./echo"
        ]
        self.assertEqual(
            {
                (entry["player_key"], entry["message"]["text"])
                for entry in room_echoes
            },
            {
                (self.player.key, "The ferry rocks against the pier."),
                (observer.key, "The ferry rocks against the pier."),
            },
        )

    def test_mob_command_before_currency_debit_commits_atomically(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 20},
            reason="test.setup",
            emit_event=False,
        )
        charon_definition = MobDefinition.objects.create(
            world=self.world,
            slug="charon",
            name="Charon",
        )
        charon = charon_definition.spawn(self.room, self.spawn_world)
        trigger = self._create_trigger(
            match="pay charon",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": {
                                "type": "mob",
                                "room": "trigger_room",
                                "mob": "mobdefinition.charon",
                            },
                            "command": "emote grunts satisfactorily.",
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "pay charon")

        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            10,
        )
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        command_snapshot = run.steps[0]["actions"][0]
        self.assertEqual(
            command_snapshot["subject"]["mob_definition_id"],
            charon_definition.id,
        )
        self.assertTrue(any(
            entry["player_key"] == self.player.key
            and entry["message"]["type"]
            == "notification.cmd.emote.success"
            and entry["message"]["data"]["actor"]["key"] == charon.key
            and entry["message"]["data"]["text"]
            == "grunts satisfactorily."
            for entry in messages
        ))

    def test_ferry_step_preserves_authored_output_order_and_transfers_actor(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 20},
            reason="test.setup",
            emit_event=False,
        )
        charon_definition = MobDefinition.objects.create(
            world=self.world,
            slug="ferry-charon",
            name="Charon",
        )
        charon_definition.spawn(self.room, self.spawn_world)
        destination = self.room.create_at("east")
        destination_ref = (
            f"room@{destination.x},{destination.y},{destination.z}"
        )
        trigger = self._create_trigger(
            match="cross acheron",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": {
                                "type": "mob",
                                "room": "trigger_room",
                                "mob": "mobdefinition.ferry-charon",
                            },
                            "command": "emote grunts satisfactorily.",
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                        {
                            "type": "command",
                            "subject": {
                                "type": "mob",
                                "room": "trigger_room",
                                "mob": "mobdefinition.ferry-charon",
                            },
                            "command": "say Get on board.",
                        },
                        {
                            "type": "command",
                            "subject": "trigger_room",
                            "command": (
                                f"/transfer {{{{ actor_key }}}} "
                                f"{destination_ref}"
                            ),
                        },
                    ],
                },
            ],
        )
        location_sequence_before = self.player.location_sequence

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "cross acheron")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        self.assertEqual(
            self.player.location_sequence,
            location_sequence_before + 1,
        )
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            10,
        )
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)

        relevant_types = [
            entry["message"]["type"]
            for entry in messages
            if (
                entry["player_key"] == self.player.key
                and entry["message"]["type"]
                in {
                    "notification.cmd.emote.success",
                    "notification.trigger.currency_debited",
                    "notification.cmd.say.success",
                    "affect.transfer",
                    "currency.balances_changed",
                }
            )
        ]
        self.assertEqual(
            relevant_types,
            [
                "notification.cmd.emote.success",
                "notification.trigger.currency_debited",
                "notification.cmd.say.success",
                "affect.transfer",
                "currency.balances_changed",
            ],
        )

    def test_transfer_before_debit_and_player_say_uses_destination_context(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 10},
            reason="test.setup",
            emit_event=False,
        )
        origin_observer = self.create_player(
            "Origin Observer",
            user=self.create_user("transfer-origin-step@example.com"),
        )
        origin_observer.in_game = True
        origin_observer.save(update_fields=["in_game"])
        destination = self.room.create_at("east")
        destination_observer = self.create_player(
            "Destination Observer",
            user=self.create_user("transfer-destination-step@example.com"),
            room=destination,
        )
        destination_observer.in_game = True
        destination_observer.save(update_fields=["in_game"])
        destination_ref = (
            f"room@{destination.x},{destination.y},{destination.z}"
        )
        trigger = self._create_trigger(
            match="board immediately",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": f"/transfer self {destination_ref}",
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "say I am aboard.",
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "board immediately")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        self.assertEqual(
            ScheduledTriggerRun.objects.get(trigger=trigger).status,
            ScheduledTriggerRun.STATUS_COMPLETED,
        )
        destination_messages = [
            entry["message"]["type"]
            for entry in messages
            if entry["player_key"] == destination_observer.key
        ]
        self.assertIn(
            "notification.trigger.currency_debited",
            destination_messages,
        )
        self.assertIn("notification.cmd.say.success", destination_messages)
        origin_message_types = {
            entry["message"]["type"]
            for entry in messages
            if entry["player_key"] == origin_observer.key
        }
        self.assertNotIn(
            "notification.trigger.currency_debited",
            origin_message_types,
        )
        self.assertNotIn("notification.cmd.say.success", origin_message_types)

    def test_invalid_transfer_rolls_back_earlier_output_and_currency(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 10},
            reason="test.setup",
            emit_event=False,
        )
        charon_definition = MobDefinition.objects.create(
            world=self.world,
            slug="rollback-charon",
            name="Charon",
        )
        charon_definition.spawn(self.room, self.spawn_world)
        trigger = self._create_trigger(
            match="broken crossing",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": {
                                "type": "mob",
                                "room": "trigger_room",
                                "mob": "mobdefinition.rollback-charon",
                            },
                            "command": "emote waves the traveler onward.",
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                        {
                            "type": "command",
                            "subject": "trigger_room",
                            "command": (
                                "/transfer {{ actor_key }} "
                                "room@999999,999999,999999"
                            ),
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            result = start_trigger_steps(
                trigger=trigger,
                actor=self.player,
                room=self.room,
            )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "invalid_room")
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            10,
        )
        self.assertFalse(ScheduledTriggerRun.objects.filter(trigger=trigger).exists())
        self.assertFalse(GameEventOutbox.objects.exists())
        self.assertFalse(any(
            entry["message"]["type"]
            in {
                "notification.cmd.emote.success",
                "notification.trigger.currency_debited",
                "affect.transfer",
            }
            for entry in messages
        ))

    def test_trigger_step_transfer_rejects_non_trigger_actor_target(self):
        destination = self.room.create_at("east")
        destination_ref = (
            f"room@{destination.x},{destination.y},{destination.z}"
        )
        other = self.create_player(
            "Other Traveler",
            user=self.create_user("other-trigger-transfer@example.com"),
        )
        other.in_game = True
        other.save(update_fields=["in_game"])
        trigger = self._create_trigger(
            match="move someone else",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_room",
                            "command": (
                                f"/transfer {other.key} {destination_ref}"
                            ),
                        },
                    ],
                },
            ],
        )

        result = start_trigger_steps(
            trigger=trigger,
            actor=self.player,
            room=self.room,
        )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "unsupported_transfer_target")
        other.refresh_from_db()
        self.assertEqual(other.room_id, self.room.id)

    def test_trigger_step_transfer_fails_fast_for_active_player_combat(self):
        destination = self.room.create_at("east")
        destination_ref = (
            f"room@{destination.x},{destination.y},{destination.z}"
        )
        trigger = self._create_trigger(
            match="leave duel",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_room",
                            "command": (
                                f"/transfer {{{{ actor_key }}}} "
                                f"{destination_ref}"
                            ),
                        },
                    ],
                },
            ],
        )

        with patch(
            "spawns.actions.builder.TransferAction._active_pvp_encounter_ids",
            return_value=[123],
        ):
            result = start_trigger_steps(
                trigger=trigger,
                actor=self.player,
                room=self.room,
            )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "target_busy")
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, self.room.id)

    def test_trigger_step_transfer_fires_destination_mob_reaction(self):
        destination = self.room.create_at("east")
        destination_ref = (
            f"room@{destination.x},{destination.y},{destination.z}"
        )
        watcher_definition = MobDefinition.objects.create(
            world=self.world,
            slug="step-transfer-watcher",
            name="Threshold Watcher",
        )
        watcher = watcher_definition.spawn(destination, self.spawn_world)
        Trigger.objects.create(
            world=self.world,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            target_type=ContentType.objects.get_for_model(MobDefinition),
            target_id=watcher_definition.id,
            event=adv_consts.MOB_REACTION_EVENT_ENTERING,
            script="say Welcome aboard.",
            display_action_in_room=False,
            gate_delay=0,
        )
        self._create_trigger(
            match="board watched ferry",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_room",
                            "command": (
                                f"/transfer {{{{ actor_key }}}} "
                                f"{destination_ref}"
                            ),
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "board watched ferry")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        self.assertTrue(any(
            entry["player_key"] == self.player.key
            and entry["message"]["type"] == "notification.cmd.say.success"
            and entry["message"]["data"]["actor"]["key"] == watcher.key
            and entry["message"]["data"]["text"] == "Welcome aboard."
            for entry in messages
        ))

    def test_trigger_step_transfer_starts_destination_aggro_once(self):
        self.world.config.combat_resolution_interval = 1.5
        self.world.config.save(update_fields=["combat_resolution_interval"])
        destination = self.room.create_at("east")
        destination_ref = (
            f"room@{destination.x},{destination.y},{destination.z}"
        )
        hostile = Mob.objects.create(
            world=self.spawn_world,
            room=destination,
            name="Acheron Sentinel",
            keywords="sentinel",
            health=20,
            health_max=20,
            attack_power=4,
            aggression=adv_consts.MOB_AGGRESSION_ALL,
        )
        self._create_trigger(
            match="board hostile ferry",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": f"/transfer self {destination_ref}",
                        },
                    ],
                },
            ],
        )

        with patch(
            "spawns.tasks.resolve_combat_encounter.apply_async"
        ) as schedule_mock:
            with capture_game_messages() as messages:
                self._dispatch(self.player.id, "board hostile ferry")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        self.assertEqual(
            CombatEncounter.objects.filter(
                player=self.player,
                mob=hostile,
                status=CombatEncounter.STATUS_ACTIVE,
            ).count(),
            1,
        )
        schedule_mock.assert_called_once()
        self.assertTrue(any(
            entry["player_key"] == self.player.key
            and entry["message"]["type"] == "cmd.kill.success"
            and entry["message"]["text"] == "Acheron Sentinel attacks you!"
            for entry in messages
        ))

    def test_insufficient_currency_prevents_mob_command_output(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 9},
            reason="test.setup",
            emit_event=False,
        )
        charon_definition = MobDefinition.objects.create(
            world=self.world,
            slug="unpaid-charon",
            name="Charon",
        )
        charon_definition.spawn(self.room, self.spawn_world)
        trigger = self._create_trigger(
            match="underpay charon",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": {
                                "type": "mob",
                                "room": "trigger_room",
                                "mob": "mobdefinition.unpaid-charon",
                            },
                            "command": "emote grunts satisfactorily.",
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "underpay charon")

        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            9,
        )
        self.assertFalse(
            ScheduledTriggerRun.objects.filter(trigger=trigger).exists()
        )
        self.assertFalse(any(
            entry["message"]["type"]
            in {
                "cmd.emote.success",
                "notification.cmd.emote.success",
                "notification.trigger.currency_debited",
            }
            for entry in messages
        ))

    def test_command_failure_rolls_back_a_preceding_currency_debit(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 10},
            reason="test.setup",
            emit_event=False,
        )
        trigger = self._create_trigger(
            match="unsafe crossing",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "north",
                        },
                    ],
                },
            ],
        )

        result = start_trigger_steps(
            trigger=trigger,
            actor=self.player,
            room=self.room,
        )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "command_not_step_safe")
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            10,
        )
        self.assertFalse(ScheduledTriggerRun.objects.filter(trigger=trigger).exists())
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_later_command_failure_suppresses_earlier_command_output(self):
        trigger = self._create_trigger(
            match="broken oath",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "say This must never be heard.",
                        },
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "north",
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            result = start_trigger_steps(
                trigger=trigger,
                actor=self.player,
                room=self.room,
            )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "command_not_step_safe")
        self.assertFalse(
            ScheduledTriggerRun.objects.filter(trigger=trigger).exists()
        )
        self.assertFalse(GameEventOutbox.objects.exists())
        self.assertFalse(any(
            entry["message"]["type"]
            in {
                "cmd.say.success",
                "notification.cmd.say.success",
            }
            for entry in messages
        ))

    def test_muted_forced_player_say_rolls_back_currency_debit(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 10},
            reason="test.setup",
            emit_event=False,
        )
        self.player.is_muted = True
        self.player.save(update_fields=["is_muted"])
        trigger = self._create_trigger(
            match="muted oath",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "say I swear the oath.",
                        },
                    ],
                },
            ],
        )

        result = start_trigger_steps(
            trigger=trigger,
            actor=self.player,
            room=self.room,
        )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "muted")
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            10,
        )
        self.assertFalse(ScheduledTriggerRun.objects.filter(trigger=trigger).exists())
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_mob_command_requires_exactly_one_room_local_subject(self):
        charon_definition = MobDefinition.objects.create(
            world=self.world,
            slug="ambiguous-charon",
            name="Charon",
        )
        charon_definition.spawn(self.room, self.spawn_world)
        charon_definition.spawn(self.room, self.spawn_world)
        trigger = self._create_trigger(
            match="hail charon",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": {
                                "type": "mob",
                                "room": "trigger_room",
                                "mob": "mobdefinition.ambiguous-charon",
                            },
                            "command": "emote looks up.",
                        },
                    ],
                },
            ],
        )

        result = start_trigger_steps(
            trigger=trigger,
            actor=self.player,
            room=self.room,
        )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "command_subject_ambiguous")
        self.assertFalse(ScheduledTriggerRun.objects.filter(trigger=trigger).exists())
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_debit_currency_does_not_reveal_an_invisible_player(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 10},
            reason="test.setup",
            emit_event=False,
        )
        observer = self.create_player(
            "Observer",
            user=self.create_user("invisible-currency-observer@example.com"),
        )
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        self.player.is_invisible = True
        self.player.save(update_fields=["is_invisible"])
        self._create_trigger(
            match="pay hidden toll",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "pay hidden toll")

        debit_messages = [
            entry
            for entry in messages
            if (
                entry["message"]["type"]
                == "notification.trigger.currency_debited"
            )
        ]
        self.assertEqual(
            [
                (entry["player_key"], entry["message"]["text"])
                for entry in debit_messages
            ],
            [(self.player.key, "You part with 10 obols.")],
        )
        self.assertNotIn(
            observer.key,
            [entry["player_key"] for entry in debit_messages],
        )

    def test_debit_currency_insufficient_funds_rolls_back_without_success_text(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 9},
            reason="test.setup",
            emit_event=False,
        )
        seed = self.seed.spawn(self.player, self.spawn_world)
        observer = self.create_player(
            "Observer",
            user=self.create_user("poor-currency-observer@example.com"),
        )
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        trigger = self._create_trigger(
            match="pay impossible toll",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "consume_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.barley-seed",
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The toll gate opens.",
                        },
                    ],
                },
            ],
        )
        revision_before = self.player.wallet_revision

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "pay impossible toll")

        self.player.refresh_from_db()
        self.assertEqual(self.player.wallet_revision, revision_before)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            9,
        )
        self.assertTrue(Item.objects.filter(pk=seed.id).exists())
        self.assertFalse(
            ScheduledTriggerRun.objects.filter(trigger=trigger).exists()
        )
        self.assertFalse(GameEventOutbox.objects.exists())
        self.assertFalse(
            any(
                "part with" in str(entry["message"].get("text", "")).lower()
                or "parts with" in str(entry["message"].get("text", "")).lower()
                or entry["message"]["type"] == "notification./echo"
                for entry in messages
            )
        )
        self.assertTrue(
            any(
                "insufficient funds" in str(
                    entry["message"].get("text", "")
                ).lower()
                for entry in messages
            )
        )

    def test_multiple_currency_debits_share_one_wallet_mutation(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 11},
            reason="test.setup",
            emit_event=False,
        )
        origin_observer = self.create_player(
            "First Toll Observer",
            user=self.create_user("first-toll-observer@example.com"),
        )
        origin_observer.in_game = True
        origin_observer.save(update_fields=["in_game"])
        destination = self.room.create_at("east")
        destination_observer = self.create_player(
            "Second Toll Observer",
            user=self.create_user("second-toll-observer@example.com"),
            room=destination,
        )
        destination_observer.in_game = True
        destination_observer.save(update_fields=["in_game"])
        destination_ref = (
            f"room@{destination.x},{destination.y},{destination.z}"
        )
        self._create_trigger(
            match="pay two tolls",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 1,
                        },
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": f"/transfer self {destination_ref}",
                        },
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The first toll is counted.",
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )
        revision_before = self.player.wallet_revision

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "pay two tolls")

        self.player.refresh_from_db()
        self.assertEqual(self.player.wallet_revision, revision_before + 1)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            0,
        )
        self.assertEqual(
            [
                entry["message"]["text"]
                for entry in messages
                if (
                    entry["player_key"] == self.player.key
                    and entry["message"]["type"]
                    == "notification.trigger.currency_debited"
                )
            ],
            [
                "You part with 1 obol.",
                "You part with 10 obols.",
            ],
        )
        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, destination.id)
        self.assertEqual(
            [
                entry["message"]["type"]
                for entry in messages
                if (
                    entry["player_key"] == self.player.key
                    and entry["message"]["type"]
                    in {
                        "notification.trigger.currency_debited",
                        "affect.transfer",
                        "currency.balances_changed",
                    }
                )
            ],
            [
                "notification.trigger.currency_debited",
                "affect.transfer",
                "notification.trigger.currency_debited",
                "currency.balances_changed",
            ],
        )
        self.assertEqual(
            [
                entry["message"]["type"]
                for entry in messages
                if entry["player_key"] == origin_observer.key
                and entry["message"]["type"]
                in {
                    "notification.trigger.currency_debited",
                    "notification./transfer.exit",
                    "notification./echo",
                }
            ],
            [
                "notification.trigger.currency_debited",
                "notification./transfer.exit",
                "notification./echo",
            ],
        )
        self.assertEqual(
            [
                entry["message"]["type"]
                for entry in messages
                if entry["player_key"] == destination_observer.key
                and entry["message"]["type"]
                in {
                    "notification./transfer.enter",
                    "notification.trigger.currency_debited",
                }
            ],
            [
                "notification./transfer.enter",
                "notification.trigger.currency_debited",
            ],
        )
        self.assertFalse(any(
            entry["message"]["type"] == "notification./echo"
            and entry["player_key"]
            in {self.player.key, destination_observer.key}
            for entry in messages
        ))
        self.assertEqual(
            len([
                entry
                for entry in messages
                if entry["message"]["type"] == "currency.balances_changed"
            ]),
            1,
        )

    def test_mixed_currency_actions_share_one_wallet_mutation_and_event_order(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 10},
            reason="test.setup",
            emit_event=False,
        )
        self._create_trigger(
            match="exchange stipend",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "grant_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 7,
                        },
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The exchange is recorded.",
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 6,
                        },
                    ],
                },
            ],
        )
        revision_before = self.player.wallet_revision

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "exchange stipend")

        self.player.refresh_from_db()
        self.assertEqual(self.player.wallet_revision, revision_before + 1)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            11,
        )
        relevant_messages = [
            entry["message"]
            for entry in messages
            if (
                entry["player_key"] == self.player.key
                and entry["message"]["type"]
                in {
                    "notification.trigger.currency_granted",
                    "notification./echo",
                    "notification.trigger.currency_debited",
                    "currency.balances_changed",
                }
            )
        ]
        self.assertEqual(
            [message["type"] for message in relevant_messages],
            [
                "notification.trigger.currency_granted",
                "notification./echo",
                "notification.trigger.currency_debited",
                "currency.balances_changed",
            ],
        )
        self.assertEqual(
            relevant_messages[-1]["data"]["reason"],
            "trigger.currency_actions",
        )
        self.assertEqual(
            relevant_messages[-1]["data"]["changes"][0]["delta"],
            1,
        )

    def test_same_step_grant_cannot_subsidize_currency_debit(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 5},
            reason="test.setup",
            emit_event=False,
        )
        trigger = self._create_trigger(
            match="claim unaffordable rebate",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "grant_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 6,
                        },
                    ],
                },
            ],
        )
        revision_before = self.player.wallet_revision

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "claim unaffordable rebate")

        self.player.refresh_from_db()
        self.assertEqual(self.player.wallet_revision, revision_before)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            5,
        )
        self.assertFalse(
            ScheduledTriggerRun.objects.filter(trigger=trigger).exists()
        )
        self.assertFalse(GameEventOutbox.objects.exists())
        self.assertFalse(any(
            entry["message"]["type"]
            in {
                "notification.trigger.currency_granted",
                "notification.trigger.currency_debited",
                "currency.balances_changed",
            }
            for entry in messages
        ))
        self.assertTrue(any(
            "insufficient funds" in str(
                entry["message"].get("text", "")
            ).lower()
            for entry in messages
        ))

    def test_net_zero_currency_actions_emit_narrative_without_wallet_revision(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 10},
            reason="test.setup",
            emit_event=False,
        )
        self._create_trigger(
            match="exchange equal coins",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 5,
                        },
                        {
                            "type": "grant_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 5,
                        },
                    ],
                },
            ],
        )
        revision_before = self.player.wallet_revision

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "exchange equal coins")

        self.player.refresh_from_db()
        self.assertEqual(self.player.wallet_revision, revision_before)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            10,
        )
        self.assertEqual(
            [
                entry["message"]["type"]
                for entry in messages
                if (
                    entry["player_key"] == self.player.key
                    and entry["message"]["type"]
                    in {
                        "notification.trigger.currency_debited",
                        "notification.trigger.currency_granted",
                        "currency.balances_changed",
                    }
                )
            ],
            [
                "notification.trigger.currency_debited",
                "notification.trigger.currency_granted",
            ],
        )

    def test_grant_currency_overflow_rolls_back_without_success_text(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: MAX_CURRENCY_AMOUNT},
            reason="test.setup",
            emit_event=False,
        )
        trigger = self._create_trigger(
            match="claim excessive stipend",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "grant_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 1,
                        },
                    ],
                },
            ],
        )
        revision_before = self.player.wallet_revision

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "claim excessive stipend")

        self.player.refresh_from_db()
        self.assertEqual(self.player.wallet_revision, revision_before)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            MAX_CURRENCY_AMOUNT,
        )
        self.assertFalse(
            ScheduledTriggerRun.objects.filter(trigger=trigger).exists()
        )
        self.assertFalse(GameEventOutbox.objects.exists())
        self.assertFalse(any(
            entry["message"]["type"]
            in {
                "notification.trigger.currency_granted",
                "currency.balances_changed",
            }
            for entry in messages
        ))
        self.assertTrue(any(
            "too large" in str(entry["message"].get("text", "")).lower()
            for entry in messages
        ))

    def test_delayed_currency_debit_cancels_cleanly_when_funds_are_insufficient(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 9},
            reason="test.setup",
            emit_event=False,
        )
        trigger = self._create_trigger(
            match="start delayed toll",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "spawn_room_item",
                            "room": "trigger_room",
                            "item": "itemdefinition.barley-seedling",
                        },
                    ],
                },
                {
                    "after_seconds": 5,
                    "actions": [
                        {
                            "type": "grant_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.harvested-barley",
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )
        self._dispatch(self.player.id, "start delayed toll")
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_ACTIVE)
        self.assertEqual(self._room_items(self.seedling).count(), 1)

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = process_due_trigger_runs(now=run.next_run_ts)

        self.assertEqual(result["cancelled"], 1)
        run.refresh_from_db()
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_CANCELLED)
        self.assertEqual(run.failure_code, "insufficient_funds")
        self.assertEqual(self._room_items(self.seedling).count(), 1)
        self.assertFalse(
            self.player.inventory.filter(definition=self.harvested).exists()
        )
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            9,
        )
        self.assertFalse(
            any(
                entry["message"]["type"]
                in {
                    "currency.balances_changed",
                    "notification.trigger.currency_debited",
                    "notification.trigger.items_changed",
                }
                for entry in messages
            )
        )

    def test_delayed_currency_grant_awards_player(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        trigger = self._create_trigger(
            match="start delayed stipend",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The stipend is being prepared.",
                        },
                    ],
                },
                {
                    "after_seconds": 5,
                    "actions": [
                        {
                            "type": "grant_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )
        revision_before = self.player.wallet_revision
        self._dispatch(self.player.id, "start delayed stipend")
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_ACTIVE)
        self.assertFalse(
            PlayerCurrencyBalance.objects.filter(
                player=self.player,
                currency=obol,
            ).exists()
        )

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = process_due_trigger_runs(now=run.next_run_ts)

        self.assertEqual(result["completed"], 1)
        self.player.refresh_from_db()
        self.assertEqual(self.player.wallet_revision, revision_before + 1)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            10,
        )
        self.assertTrue(any(
            entry["player_key"] == self.player.key
            and entry["message"]["type"]
            == "notification.trigger.currency_granted"
            and entry["message"]["text"] == "You receive 10 obols."
            for entry in messages
        ))

    def test_delayed_currency_debit_notifies_players_in_actors_current_room(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 10},
            reason="test.setup",
            emit_event=False,
        )
        original_observer = self.create_player(
            "Original Observer",
            user=self.create_user("original-currency-observer@example.com"),
        )
        original_observer.in_game = True
        original_observer.save(update_fields=["in_game"])
        other_room = self.room.create_at("north")
        current_observer = self.create_player(
            "Current Observer",
            user=self.create_user("current-currency-observer@example.com"),
        )
        current_observer.room = other_room
        current_observer.in_game = True
        current_observer.save(update_fields=["room", "in_game"])
        trigger = self._create_trigger(
            match="start moving toll",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The toll will come due.",
                        },
                    ],
                },
                {
                    "after_seconds": 5,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )
        self._dispatch(self.player.id, "start moving toll")
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.player.room = other_room
        self.player.save(update_fields=["room"])

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = process_due_trigger_runs(now=run.next_run_ts)

        self.assertEqual(result["completed"], 1)
        debit_messages = [
            entry
            for entry in messages
            if (
                entry["message"]["type"]
                == "notification.trigger.currency_debited"
            )
        ]
        self.assertEqual(
            [
                (entry["player_key"], entry["message"]["text"])
                for entry in debit_messages
            ],
            [
                (self.player.key, "You part with 10 obols."),
                (current_observer.key, "Joe parts with 10 obols."),
            ],
        )
        self.assertNotIn(
            original_observer.key,
            [entry["player_key"] for entry in debit_messages],
        )
        self.assertTrue(
            all(
                entry["message"]["data"]["room"]["key"] == other_room.key
                for entry in debit_messages
            )
        )

    def test_debit_step_replacement_keeps_later_consume_candidates_bounded(self):
        obol = create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.player,
            {obol: 10},
            reason="test.setup",
            emit_event=False,
        )
        trigger = self._create_trigger(
            match="start replacement toll",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "spawn_room_item",
                            "room": "trigger_room",
                            "item": "itemdefinition.barley-mature",
                            "bind": "crop",
                        },
                    ],
                },
                {
                    "after_seconds": 5,
                    "actions": [
                        {
                            "type": "replace_room_item",
                            "target": "crop",
                            "with": "itemdefinition.barley-seedling",
                        },
                        {
                            "type": "consume_room_item",
                            "room": "trigger_room",
                            "item": "itemdefinition.barley-mature",
                            "count": 2,
                        },
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )
        self._dispatch(self.player.id, "start replacement toll")
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        bound_item_id = run.bindings["crop"]["id"]
        decoys = [
            self.mature.spawn(self.room, self.spawn_world)
            for _index in range(2)
        ]
        self.assertLess(bound_item_id, min(item.id for item in decoys))

        with self.captureOnCommitCallbacks(execute=True):
            result = process_due_trigger_runs(now=run.next_run_ts)

        self.assertEqual(result["completed"], 1)
        run.refresh_from_db()
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        self.assertFalse(self._room_items(self.mature).exists())
        self.assertEqual(self._room_items(self.seedling).count(), 1)
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.player,
                currency=obol,
            ).amount,
            0,
        )

    def test_currency_debit_rejects_a_mob_trigger_actor(self):
        create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        trigger = self._create_trigger(
            match="mob pays toll",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )
        mob = self.create_mob("Toll Collector")

        result = start_trigger_steps(
            trigger=trigger,
            actor=mob,
            room=self.room,
        )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "invalid_actor")
        self.assertFalse(
            ScheduledTriggerRun.objects.filter(trigger=trigger).exists()
        )
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_currency_grant_rejects_a_mob_trigger_actor(self):
        create_currency(
            world=self.world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        trigger = self._create_trigger(
            match="mob claims stipend",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "grant_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
        )
        mob = self.create_mob("Stipend Collector")

        result = start_trigger_steps(
            trigger=trigger,
            actor=mob,
            room=self.room,
        )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "invalid_actor")
        self.assertFalse(
            ScheduledTriggerRun.objects.filter(trigger=trigger).exists()
        )
        self.assertFalse(GameEventOutbox.objects.exists())

    def test_set_mob_action_normalizes_fields_where_and_state(self):
        normalized = normalize_trigger_steps([
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "set_mob",
                        "room": "trigger_room",
                        "mob": "mobdefinition.captive-commander",
                        "where": {
                            "eq": ["state.character.captive", True],
                        },
                        "fields": {
                            "name": "a freed Greek commander",
                            "room_description": "A freed commander stands here.",
                            "description": "The commander studies the camp.",
                            "attackable": True,
                        },
                        "state": {
                            "captive": False,
                        },
                    },
                ],
            },
        ])

        self.assertEqual(
            normalized[0]["actions"][0],
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.captive-commander",
                "where": {
                    "eq": ["state.character.captive", True],
                },
                "fields": {
                    "name": "a freed Greek commander",
                    "room_description": "A freed commander stands here.",
                    "description": "The commander studies the camp.",
                    "attackable": True,
                },
                "state": {
                    "captive": False,
                },
            },
        )

    def test_set_mob_action_rejects_invalid_fields_and_context(self):
        invalid_actions = [
            {
                "type": "set_mob",
                "room": "other_room",
                "mob": "mobdefinition.commander",
                "fields": {"attackable": True},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "itemdefinition.commander",
                "fields": {"attackable": True},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "fields": {},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "fields": {"attackable": "yes"},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "fields": {"name": "   "},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "fields": {"keywords": "commander"},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "where": {"unknown_operator": True},
                "fields": {"attackable": True},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "where": "state.character.captive",
                "fields": {"attackable": True},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "where": {"all": ["state.character.captive"]},
                "fields": {"attackable": True},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "where": {
                    "mob_present": "mobdefinition.other-commander",
                },
                "fields": {"attackable": True},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "where": {"eq": ["state.room.cage_open", False]},
                "fields": {"attackable": True},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "where": {
                    "eq": [
                        "actor.name",
                        "mobdefinition.other-commander",
                    ],
                },
                "fields": {"attackable": True},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "fields": {"name": "x" * 256},
            },
            {
                "type": "set_mob",
                "room": "trigger_room",
                "mob": "mobdefinition.commander",
                "fields": {"attackable": True},
                "state": {"   ": False},
            },
        ]

        for action in invalid_actions:
            with self.subTest(action=action):
                with self.assertRaises(TriggerStepSpecError):
                    normalize_trigger_steps([
                        {
                            "after_seconds": 0,
                            "actions": [action],
                        },
                    ])

    def test_harvest_actions_reject_invalid_fields_and_context_refs(self):
        invalid_actions = [
            {
                "type": "consume_room_item",
                "item": "itemdefinition.barley-mature",
            },
            {
                "type": "consume_room_item",
                "room": "some_room",
                "item": "itemdefinition.barley-mature",
            },
            {
                "type": "consume_room_item",
                "room": "trigger_room",
                "item": "itemdefinition.barley-mature",
                "actor": "trigger_actor",
            },
            {
                "type": "grant_item",
                "item": "itemdefinition.harvested-barley",
            },
            {
                "type": "grant_item",
                "actor": "some_actor",
                "item": "itemdefinition.harvested-barley",
            },
            {
                "type": "grant_item",
                "actor": "trigger_actor",
                "item": "itemdefinition.harvested-barley",
                "count": 0,
            },
            {
                "type": "grant_item",
                "actor": "trigger_actor",
                "item": "itemdefinition.harvested-barley",
                "count": True,
            },
            {
                "type": "grant_item",
                "actor": "trigger_actor",
                "item": "mobdefinition.barley",
            },
        ]

        for action in invalid_actions:
            with self.subTest(action=action):
                with self.assertRaises(TriggerStepSpecError):
                    normalize_trigger_steps([
                        {
                            "after_seconds": 0,
                            "actions": [action],
                        },
                    ])

    def test_set_mob_atomically_updates_runtime_mob_state_and_consumes_item(self):
        self.use_imported_room(
            relative_id=self.room.id + 5000,
            x=self.room.x + 1,
        )
        commander_definition = MobDefinition.objects.create(
            world=self.world,
            slug="captive-commander",
            name="a captive Greek commander",
            room_description="A captive commander sits in a cage.",
            description="Ropes bind the commander's wrists.",
            initial_state={
                "captive": True,
                "rank": "commander",
            },
            attackable=False,
        )
        commander = commander_definition.spawn(self.room, self.spawn_world)
        key = self.seed.spawn(self.player, self.spawn_world)
        trigger = self._create_trigger(
            match="open cage",
            conditions=self._inventory_condition(),
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "consume_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.barley-seed",
                        },
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": "mobdefinition.captive-commander",
                            "where": {
                                "all": [
                                    {
                                        "eq": [
                                            "state.character.captive",
                                            True,
                                        ],
                                    },
                                    {
                                        "eq": [
                                            "player.id",
                                            self.player.id,
                                        ],
                                    },
                                ],
                            },
                            "fields": {
                                "name": "a freed Greek commander",
                                "room_description": "A freed commander stands here.",
                                "description": "The commander studies the camp.",
                                "attackable": True,
                            },
                            "state": {
                                "captive": False,
                            },
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "open cage")

        self.assertFalse(Item.objects.filter(pk=key.id).exists())
        commander.refresh_from_db()
        self.assertEqual(commander.name, "a freed Greek commander")
        self.assertEqual(
            commander.room_description,
            "A freed commander stands here.",
        )
        self.assertEqual(
            commander.description,
            "The commander studies the camp.",
        )
        self.assertTrue(commander.attackable)
        self.assertFalse(
            get_state_snapshot(STATE_SCOPE_CHARACTER, commander)["captive"]
        )
        self.assertEqual(
            get_state_snapshot(STATE_SCOPE_CHARACTER, commander)["rank"],
            "commander",
        )
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        self.assertEqual(
            run.steps[0]["actions"][1]["mob_definition_id"],
            commander_definition.id,
        )
        mob_change = next(
            entry["message"]
            for entry in messages
            if entry["message"]["type"] == "notification.trigger.mobs_changed"
        )
        self.assertEqual(
            mob_change["data"]["room"]["key"],
            f"room.{self.room.relative_id}",
        )
        self.assertEqual(
            mob_change["data"]["mobs"][0]["key"],
            commander.key,
        )
        self.assertEqual(
            mob_change["data"]["mobs"][0]["name"],
            "a freed Greek commander",
        )
        self.assertTrue(mob_change["data"]["mobs"][0]["attackable"])
        self.assertNotIn("actions", mob_change["data"]["mobs"][0])
        self.assertNotIn("quest_indicator", mob_change["data"]["mobs"][0])

    def test_mob_mixed_steps_lock_actor_and_targets_in_stable_id_order(self):
        from spawns import trigger_steps as trigger_step_runtime

        target_definition = MobDefinition.objects.create(
            world=self.world,
            slug="lock-order-target",
            name="a lock-order target",
        )
        actor_definition = MobDefinition.objects.create(
            world=self.world,
            slug="lock-order-actor",
            name="a lock-order actor",
        )
        target = target_definition.spawn(self.room, self.spawn_world)
        actor = actor_definition.spawn(self.room, self.spawn_world)
        self.assertLess(target.id, actor.id)
        trigger = self._create_trigger(
            match="delayed mob lock order",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": "mobdefinition.lock-order-target",
                            "fields": {"name": "a changed target"},
                        },
                        {
                            "type": "grant_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.barley-seed",
                        },
                    ],
                },
                {
                    "after_seconds": 1,
                    "actions": [
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": "mobdefinition.lock-order-target",
                            "fields": {
                                "description": "A safely locked target.",
                            },
                        },
                        {
                            "type": "grant_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.barley-seed",
                        },
                    ],
                },
            ],
        )
        locked_batches = []
        original_mob_lock = trigger_step_runtime._lock_mob_rows

        def record_mob_locks(**kwargs):
            locked_ids = original_mob_lock(**kwargs)
            locked_batches.append(locked_ids)
            return locked_ids

        with (
            patch(
                "spawns.trigger_steps._lock_mob_rows",
                side_effect=record_mob_locks,
            ),
            patch("spawns.trigger_steps._flush_queued_events"),
        ):
            result = start_trigger_steps(
                trigger=trigger,
                actor=actor,
                room=self.room,
            )
        self.assertTrue(result.started)
        self.assertEqual(locked_batches, [(target.id, actor.id)])
        run = ScheduledTriggerRun.objects.get(pk=result.run_id)

        locked_batches.clear()
        with (
            patch(
                "spawns.trigger_steps._lock_mob_rows",
                side_effect=record_mob_locks,
            ),
            patch("spawns.trigger_steps._flush_queued_events"),
        ):
            due_result = process_due_trigger_runs(now=run.next_run_ts)

        self.assertEqual(locked_batches, [(target.id, actor.id)])
        self.assertEqual(due_result["completed"], 1)
        target.refresh_from_db()
        self.assertEqual(target.name, "a changed target")
        self.assertEqual(
            target.description,
            "A safely locked target.",
        )
        self.assertEqual(
            actor.inventory.filter(definition=self.seed).count(),
            2,
        )

    def test_rejected_mob_start_does_not_prelock_step_items(self):
        from spawns import trigger_steps as trigger_step_runtime

        target_definition = MobDefinition.objects.create(
            world=self.world,
            slug="rejected-lock-target",
            name="a rejected lock target",
        )
        actor_definition = MobDefinition.objects.create(
            world=self.world,
            slug="rejected-lock-actor",
            name="a rejected lock actor",
        )
        target_definition.spawn(self.room, self.spawn_world)
        actor = actor_definition.spawn(self.room, self.spawn_world)
        trigger = self._create_trigger(
            match="rejected mob lock",
            conditions=self._inventory_condition(),
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": "mobdefinition.rejected-lock-target",
                            "fields": {"name": "an unreachable change"},
                        },
                        {
                            "type": "consume_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.barley-seed",
                            "count": 1000,
                        },
                    ],
                },
            ],
        )

        with (
            patch(
                "spawns.trigger_steps._prelock_step_resources",
                wraps=trigger_step_runtime._prelock_step_resources,
            ) as prelock_resources,
            patch("spawns.trigger_steps._flush_queued_events"),
        ):
            result = start_trigger_steps(
                trigger=trigger,
                actor=actor,
                room=self.room,
            )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "conditions_failed")
        prelock_resources.assert_not_called()

    def test_set_mob_field_only_update_keeps_character_state_sparse(self):
        commander_definition = MobDefinition.objects.create(
            world=self.world,
            slug="stateless-commander",
            name="a quiet commander",
        )
        commander = commander_definition.spawn(self.room, self.spawn_world)
        self.assertFalse(MobState.objects.filter(mob=commander).exists())
        self._create_trigger(
            match="name commander",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": "mobdefinition.stateless-commander",
                            "fields": {
                                "name": "a named commander",
                            },
                        },
                    ],
                },
            ],
        )

        self._dispatch(self.player.id, "name commander")

        commander.refresh_from_db()
        self.assertEqual(commander.name, "a named commander")
        self.assertFalse(MobState.objects.filter(mob=commander).exists())

    def test_set_mob_unions_partial_event_fields_for_repeated_updates(self):
        commander_definition = MobDefinition.objects.create(
            world=self.world,
            slug="twice-updated-commander",
            name="a captive commander",
            attackable=False,
        )
        commander = commander_definition.spawn(self.room, self.spawn_world)
        self._create_trigger(
            match="update commander twice",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": "mobdefinition.twice-updated-commander",
                            "fields": {
                                "name": "a freed commander",
                            },
                        },
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": "mobdefinition.twice-updated-commander",
                            "fields": {
                                "attackable": True,
                            },
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "update commander twice")

        mob_change = next(
            entry["message"]
            for entry in messages
            if entry["message"]["type"] == "notification.trigger.mobs_changed"
        )
        self.assertEqual(len(mob_change["data"]["mobs"]), 1)
        self.assertEqual(
            mob_change["data"]["mobs"][0],
            {
                "key": commander.key,
                "name": "a freed commander",
                "attackable": True,
            },
        )

    def test_mob_change_event_builds_canonical_delta_without_queries(self):
        commander_definition = MobDefinition.objects.create(
            world=self.world,
            slug="event-commander",
            name="a definition commander",
            room_description="a definition commander waits here.",
            description="Definition description.",
            attackable=False,
        )
        commander = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=commander_definition,
            name="",
            room_description="",
            description="",
            attackable=True,
        )
        commander = Mob.objects.select_related("definition").get(
            pk=commander.id,
        )
        changes = TriggerMobChanges(updated={
            commander.id: TriggerMobChange(
                mob=commander,
                fields={
                    "name",
                    "room_description",
                    "description",
                    "attackable",
                },
            ),
        })

        with self.assertNumQueries(0):
            events = _mob_change_events(
                run=SimpleNamespace(actor_key=self.player.key),
                room=self.room,
                changes=changes,
                room_recipient_keys=(self.player.key,),
            )

        self.assertEqual(
            events[0].data["mobs"][0],
            {
                "key": commander.key,
                "name": "a definition commander",
                "room_description": "A definition commander waits here.",
                "description": "Definition description.",
                "attackable": True,
            },
        )

    def test_set_mob_zero_match_rolls_back_item_consumption(self):
        commander_definition = MobDefinition.objects.create(
            world=self.world,
            slug="freed-commander",
            name="a freed commander",
            initial_state={"captive": False},
        )
        commander = commander_definition.spawn(self.room, self.spawn_world)
        key = self.seed.spawn(self.player, self.spawn_world)
        self._create_trigger(
            match="open empty cage",
            conditions=self._inventory_condition(),
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "consume_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.barley-seed",
                        },
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": "mobdefinition.freed-commander",
                            "where": {
                                "eq": [
                                    "state.character.captive",
                                    True,
                                ],
                            },
                            "fields": {
                                "name": "should not change",
                            },
                        },
                    ],
                },
            ],
        )

        self._dispatch(self.player.id, "open empty cage")

        self.assertTrue(Item.objects.filter(pk=key.id).exists())
        commander.refresh_from_db()
        self.assertEqual(commander.name, "a freed commander")
        self.assertFalse(ScheduledTriggerRun.objects.exists())

    def test_set_mob_ambiguous_match_rolls_back_every_action(self):
        commander_definition = MobDefinition.objects.create(
            world=self.world,
            slug="two-captive-commanders",
            name="a captive commander",
            initial_state={"captive": True},
        )
        commanders = [
            commander_definition.spawn(self.room, self.spawn_world)
            for _ in range(2)
        ]
        key = self.seed.spawn(self.player, self.spawn_world)
        self._create_trigger(
            match="open crowded cage",
            conditions=self._inventory_condition(),
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "consume_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.barley-seed",
                        },
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": "mobdefinition.two-captive-commanders",
                            "where": {
                                "eq": [
                                    "state.character.captive",
                                    True,
                                ],
                            },
                            "fields": {
                                "name": "should not change",
                            },
                            "state": {
                                "captive": False,
                            },
                        },
                    ],
                },
            ],
        )

        self._dispatch(self.player.id, "open crowded cage")

        self.assertTrue(Item.objects.filter(pk=key.id).exists())
        self.assertEqual(
            list(
                Mob.objects.filter(pk__in=[mob.id for mob in commanders])
                .order_by("id")
                .values_list("name", flat=True)
            ),
            ["a captive commander", "a captive commander"],
        )
        self.assertTrue(
            all(
                get_state_snapshot(STATE_SCOPE_CHARACTER, mob)["captive"]
                for mob in commanders
            )
        )
        self.assertFalse(ScheduledTriggerRun.objects.exists())

    def test_grant_item_aggregate_count_per_step_cannot_exceed_32(self):
        actions = [
            {
                "type": "grant_item",
                "actor": "trigger_actor",
                "item": "itemdefinition.harvested-barley",
                "count": count,
            }
            for count in (17, 16)
        ]

        with self.assertRaises(TriggerStepSpecError):
            normalize_trigger_steps([
                {
                    "after_seconds": 0,
                    "actions": actions,
                },
            ])

        actions[0]["count"] = 16
        normalized = normalize_trigger_steps([
            {
                "after_seconds": 0,
                "actions": actions,
            },
        ])
        self.assertEqual(
            sum(action["count"] for action in normalized[0]["actions"]),
            32,
        )

    def test_harvest_atomically_consumes_room_item_and_grants_inventory_item(self):
        mature_item = self.mature.spawn(self.room, self.spawn_world)
        lookalike = self.growing.spawn(self.room, self.spawn_world)
        lookalike.name = self.mature.name
        lookalike.save(update_fields=["name"])
        trigger = self._create_trigger(
            match="harvest barley",
            steps=self._harvest_steps(),
            conditions=self._harvest_conditions(),
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "harvest barley")

        self.assertFalse(Item.objects.filter(pk=mature_item.id).exists())
        self.assertTrue(Item.objects.filter(pk=lookalike.id).exists())
        harvested_item = self.player.inventory.get(definition=self.harvested)
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)

        item_change_messages = [
            entry["message"]
            for entry in messages
            if entry["message"]["type"] == "notification.trigger.items_changed"
        ]
        self.assertEqual(len(item_change_messages), 1)
        item_change_data = item_change_messages[0]["data"]
        self.assertEqual(item_change_data["room"]["key"], self.room.key)
        self.assertEqual(
            item_change_data["room_items_removed"],
            [{"key": mature_item.key}],
        )
        self.assertEqual(item_change_data["room_items_added"], [])
        self.assertEqual(item_change_data["actor_inventory_removed"], [])
        self.assertEqual(
            [item["key"] for item in item_change_data["actor_inventory_added"]],
            [harvested_item.key],
        )
        self.assertEqual(
            item_change_data["actor_inventory_added"][0]["name"],
            self.harvested.name,
        )

    def test_harvest_inventory_delta_is_sent_only_to_the_trigger_actor(self):
        observer = self.create_player(
            "Barley Observer",
            user=self.create_user("barley-observer@example.com"),
        )
        observer.in_game = True
        observer.save(update_fields=["in_game"])
        mature_item = self.mature.spawn(self.room, self.spawn_world)
        self._create_trigger(
            match="harvest barley",
            steps=self._harvest_steps(),
            conditions=self._harvest_conditions(),
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "harvest barley")

        item_change_messages = [
            entry
            for entry in messages
            if entry["message"]["type"] == "notification.trigger.items_changed"
        ]
        harvester_message = next(
            entry["message"]
            for entry in item_change_messages
            if entry["player_key"] == self.player.key
        )
        observer_message = next(
            entry["message"]
            for entry in item_change_messages
            if entry["player_key"] == observer.key
        )
        self.assertEqual(
            [
                entry["player_key"]
                for entry in item_change_messages
            ],
            [self.player.key, observer.key],
        )

        harvester_data = harvester_message["data"]
        self.assertEqual(
            harvester_data["room_items_removed"],
            [{"key": mature_item.key}],
        )
        self.assertEqual(len(harvester_data["actor_inventory_added"]), 1)

        observer_data = observer_message["data"]
        self.assertEqual(
            observer_data["room_items_removed"],
            [{"key": mature_item.key}],
        )
        self.assertEqual(observer_data["actor_inventory_added"], [])

    def test_same_step_item_add_then_remove_emits_no_ghost_item_delta(self):
        trigger = self._create_trigger(
            match="prepare barley",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "grant_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.harvested-barley",
                        },
                        {
                            "type": "consume_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.harvested-barley",
                        },
                        {
                            "type": "spawn_room_item",
                            "room": "trigger_room",
                            "item": "itemdefinition.barley-mature",
                        },
                        {
                            "type": "consume_room_item",
                            "room": "trigger_room",
                            "item": "itemdefinition.barley-mature",
                        },
                    ],
                },
            ],
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "prepare barley")

        self.assertFalse(self.player.inventory.filter(definition=self.harvested).exists())
        self.assertFalse(self._room_items(self.mature).exists())
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        self.assertFalse(
            any(
                entry["message"]["type"] == "notification.trigger.items_changed"
                for entry in messages
            )
        )

    def test_missing_room_item_rolls_back_an_earlier_grant(self):
        steps = self._harvest_steps()
        steps[0]["actions"] = [
            steps[0]["actions"][1],
            steps[0]["actions"][0],
        ]
        trigger = self._create_trigger(
            match="harvest barley",
            steps=steps,
            conditions="",
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id, "harvest barley")

        self.assertFalse(self.player.inventory.filter(definition=self.harvested).exists())
        self.assertFalse(ScheduledTriggerRun.objects.filter(trigger=trigger).exists())
        self.assertFalse(GameEventOutbox.objects.exists())
        self.assertFalse(
            any(
                entry["message"]["type"] == "notification.trigger.items_changed"
                for entry in messages
            )
        )

    def test_harvest_room_action_is_visible_only_while_mature_barley_is_present(self):
        self._create_trigger(
            match="harvest barley",
            steps=self._harvest_steps(),
            conditions=self._harvest_conditions(),
        )

        def look_actions():
            with capture_game_messages() as messages:
                self._dispatch(self.player.id, "look")
            look_message = next(
                entry["message"]
                for entry in messages
                if entry["message"]["type"] == "cmd.look.success"
            )
            return look_message["data"]["target"]["actions"]

        self.assertNotIn("harvest barley", look_actions())

        mature_item = self.mature.spawn(self.room, self.spawn_world)
        self.assertIn("harvest barley", look_actions())

        mature_item.delete()
        self.assertNotIn("harvest barley", look_actions())

    def test_step_zero_consumes_spawns_binds_echoes_and_persists_due_run(self):
        seed_item = self.seed.spawn(self.player, self.spawn_world)
        trigger = self._create_trigger()

        with capture_game_messages() as messages:
            self._dispatch(self.player.id)

        self.assertFalse(
            self.player.inventory.filter(definition=self.seed).exists()
        )
        seedling = self._room_items(self.seedling).get()
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_ACTIVE)
        self.assertEqual(run.next_step_index, 1)
        self.assertEqual(run.bindings["crop"], {"type": "item", "id": seedling.id})
        self.assertEqual(
            run.next_run_ts,
            run.started_ts + timedelta(seconds=20),
        )
        self.assertEqual(run.steps[2]["due_after_seconds"], 40)
        self.assertTrue(
            any(
                "soft rustle" in message["message"].get("text", "")
                for message in messages
            )
        )
        item_change_messages = [
            entry["message"]
            for entry in messages
            if entry["message"]["type"] == "notification.trigger.items_changed"
        ]
        self.assertEqual(len(item_change_messages), 1)
        item_change_data = item_change_messages[0]["data"]
        self.assertEqual(item_change_data["room"]["key"], self.room.key)
        self.assertEqual(
            item_change_data["actor_inventory_removed"],
            [{"key": seed_item.key}],
        )
        self.assertEqual(item_change_data["room_items_removed"], [])
        self.assertEqual(
            [item["key"] for item in item_change_data["room_items_added"]],
            [seedling.key],
        )
        self.assertEqual(
            item_change_data["room_items_added"][0]["name"],
            self.seedling.name,
        )

    def test_step_runtime_distinguishes_typed_numeric_slugs_from_bare_ids(self):
        numeric_slug_definition = ItemDefinition.objects.create(
            world=self.world,
            slug=str(self.seed.id),
            name="a numbered seed",
        )
        self.seed.spawn(self.player, self.spawn_world)
        numeric_slug_definition.spawn(self.player, self.spawn_world)
        numeric_ref = f"itemdefinition.{self.seed.id}"
        trigger = self._create_trigger(
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "consume_item",
                            "actor": "trigger_actor",
                            "item": numeric_ref,
                        },
                    ],
                },
            ],
            conditions=json.dumps({
                "item_present": {
                    "location": "actor_inventory",
                    "item": numeric_ref,
                },
            }),
        )

        self._dispatch(self.player.id)

        self.assertTrue(
            self.player.inventory.filter(definition=self.seed).exists()
        )
        self.assertFalse(
            self.player.inventory.filter(definition=numeric_slug_definition).exists()
        )
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        self.assertEqual(
            run.steps[0]["actions"][0]["item"],
            numeric_ref,
        )
        self.assertEqual(
            run.steps[0]["actions"][0]["item_definition_id"],
            numeric_slug_definition.id,
        )

    def test_due_steps_replace_exact_binding_without_timing_drift(self):
        self.seed.spawn(self.player, self.spawn_world)
        trigger = self._create_trigger()
        self._dispatch(self.player.id)
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        bound_seedling_id = run.bindings["crop"]["id"]
        bound_seedling_key = f"item.{bound_seedling_id}"
        decoy = self.seedling.spawn(self.room, self.spawn_world)

        first_due = run.started_ts + timedelta(seconds=25)
        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                first_result = process_due_trigger_runs(now=first_due)

        self.assertEqual(first_result["processed"], 1)
        self.assertTrue(Item.objects.filter(pk=decoy.id).exists())
        self.assertFalse(Item.objects.filter(pk=bound_seedling_id).exists())
        run.refresh_from_db()
        growing_id = run.bindings["crop"]["id"]
        self.assertTrue(Item.objects.filter(pk=growing_id, definition=self.growing).exists())
        self.assertEqual(
            run.next_run_ts,
            run.started_ts + timedelta(seconds=40),
        )
        item_change_messages = [
            entry["message"]
            for entry in messages
            if entry["message"]["type"] == "notification.trigger.items_changed"
        ]
        self.assertEqual(len(item_change_messages), 1)
        item_change_data = item_change_messages[0]["data"]
        self.assertEqual(
            item_change_data["room_items_removed"],
            [{"key": bound_seedling_key}],
        )
        self.assertEqual(
            [item["key"] for item in item_change_data["room_items_added"]],
            [f"item.{growing_id}"],
        )

        second_result = process_due_trigger_runs(
            now=run.started_ts + timedelta(seconds=45),
        )
        self.assertEqual(second_result["completed"], 1)
        run.refresh_from_db()
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        self.assertFalse(Item.objects.filter(pk=growing_id).exists())
        self.assertEqual(self._room_items(self.mature).count(), 1)

        self.assertEqual(
            process_due_trigger_runs(now=run.started_ts + timedelta(seconds=60))["processed"],
            0,
        )
        self.assertEqual(self._room_items(self.mature).count(), 1)

    def test_delayed_step_stays_in_original_room_after_actor_moves(self):
        self.seed.spawn(self.player, self.spawn_world)
        trigger = self._create_trigger()
        self._dispatch(self.player.id)
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        other_room = self.room.create_at("north")
        self.player.room = other_room
        self.player.save(update_fields=["room"])

        process_due_trigger_runs(now=run.started_ts + timedelta(seconds=20))

        self.assertEqual(self._room_items(self.growing).count(), 1)
        self.assertFalse(
            Item.objects.filter(
                world=self.spawn_world,
                container_type=ContentType.objects.get_for_model(Room),
                container_id=other_room.id,
                definition=self.growing,
            ).exists()
        )

    def test_missing_bound_item_cancels_run(self):
        self.seed.spawn(self.player, self.spawn_world)
        trigger = self._create_trigger()
        self._dispatch(self.player.id)
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        Item.objects.get(pk=run.bindings["crop"]["id"]).delete()

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = process_due_trigger_runs(
                    now=run.started_ts + timedelta(seconds=20),
                )

        self.assertEqual(result["cancelled"], 1)
        run.refresh_from_db()
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_CANCELLED)
        self.assertEqual(run.failure_code, "bound_item_missing")
        self.assertFalse(self._room_items(self.growing).exists())
        self.assertFalse(
            any(
                entry["message"]["type"] == "notification.trigger.items_changed"
                for entry in messages
            )
        )

    def test_failed_action_rolls_back_earlier_actions_in_same_step(self):
        steps = self._steps()
        steps[1]["actions"].insert(-1, {
            "type": "consume_item",
            "actor": "trigger_actor",
            "item": "itemdefinition.barley-seed",
            "count": 1,
        })
        self.seed.spawn(self.player, self.spawn_world)
        trigger = self._create_trigger(steps=steps)
        self._dispatch(self.player.id)
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        seedling_id = run.bindings["crop"]["id"]

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                result = process_due_trigger_runs(
                    now=run.started_ts + timedelta(seconds=20),
                )

        self.assertEqual(result["cancelled"], 1)
        self.assertTrue(Item.objects.filter(pk=seedling_id, definition=self.seedling).exists())
        self.assertFalse(self._room_items(self.growing).exists())
        run.refresh_from_db()
        self.assertEqual(run.bindings["crop"]["id"], seedling_id)
        self.assertEqual(run.next_step_index, 1)
        self.assertEqual(GameEventOutbox.objects.count(), 0)
        self.assertFalse(
            any(
                entry["message"]["type"] == "notification.trigger.items_changed"
                for entry in messages
            )
        )

    def test_room_occupancy_condition_blocks_a_second_sequence(self):
        second_player = self.create_player(
            "Second Farmer",
            user=self.create_user("second-farmer@example.com"),
        )
        second_player.in_game = True
        second_player.save(update_fields=["in_game"])
        self.seed.spawn(self.player, self.spawn_world)
        self.seed.spawn(second_player, self.spawn_world)
        self._create_trigger()

        self._dispatch(self.player.id)
        self._dispatch(second_player.id)

        self.assertEqual(ScheduledTriggerRun.objects.count(), 1)
        self.assertEqual(self._room_items(self.seedling).count(), 1)
        self.assertTrue(second_player.inventory.filter(definition=self.seed).exists())

    def test_step_zero_failure_rolls_back_run_items_and_events(self):
        steps = [
            {
                "after_seconds": 0,
                "actions": [
                    {
                        "type": "consume_item",
                        "actor": "trigger_actor",
                        "item": "itemdefinition.barley-seed",
                    },
                    {
                        "type": "consume_item",
                        "actor": "trigger_actor",
                        "item": "itemdefinition.barley-seed",
                    },
                    {
                        "type": "echo",
                        "room": "trigger_room",
                        "text": "This echo must roll back.",
                    },
                ],
            },
        ]
        seed_item = self.seed.spawn(self.player, self.spawn_world)
        self._create_trigger(
            steps=steps,
            conditions=self._inventory_condition(),
        )

        with capture_game_messages() as messages:
            self._dispatch(self.player.id)

        self.assertTrue(Item.objects.filter(pk=seed_item.id).exists())
        self.assertFalse(ScheduledTriggerRun.objects.exists())
        self.assertFalse(GameEventOutbox.objects.exists())
        self.assertFalse(
            any(
                entry["message"]["type"]
                in {"notification./echo", "notification.trigger.items_changed"}
                for entry in messages
            )
        )

    def test_instance_local_trigger_uses_base_world_item_definitions(self):
        instance_config = WorldConfig.objects.create()
        instance_template = World.objects.new_world(
            name="Barley Cellar",
            author=self.user,
            config=instance_config,
            instance_of=self.world,
        )
        instance_room = instance_template.rooms.first()
        instance_runtime = instance_template.create_spawn_world()
        self.player.world = instance_runtime
        self.player.room = instance_room
        self.player.save(update_fields=["world", "room"])
        trigger = self._create_trigger(
            world=instance_template,
            room=instance_room,
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "spawn_room_item",
                            "room": "trigger_room",
                            "item": "itemdefinition.barley-seedling",
                        },
                    ],
                },
            ],
        )

        self._dispatch(self.player.id)

        self.assertTrue(
            Item.objects.filter(
                world=instance_runtime,
                container_type=ContentType.objects.get_for_model(Room),
                container_id=instance_room.id,
                definition=self.seedling,
                is_pending_deletion=False,
            ).exists()
        )
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.runtime_world_id, instance_runtime.id)

    def test_set_mob_resolves_base_definition_and_isolates_instance_runtime(self):
        commander_definition = MobDefinition.objects.create(
            world=self.world,
            slug="instance-captive-commander",
            name="a captive commander",
            initial_state={"captive": True},
            attackable=False,
        )
        instance_template = World.objects.new_world(
            name="Commander Cage",
            author=self.user,
            config=WorldConfig.objects.create(),
            instance_of=self.world,
        )
        instance_room = instance_template.config.starting_room
        local_runtime = instance_template.create_spawn_world(
            instance_ref="local-run",
        )
        parallel_runtime = instance_template.create_spawn_world(
            instance_ref="parallel-run",
        )
        local_commander = commander_definition.spawn(
            instance_room,
            local_runtime,
        )
        parallel_commander = commander_definition.spawn(
            instance_room,
            parallel_runtime,
        )
        self.player.world = local_runtime
        self.player.room = instance_room
        self.player.save(update_fields=["world", "room"])
        trigger = self._create_trigger(
            world=instance_template,
            room=instance_room,
            match="free commander",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": (
                                "mobdefinition."
                                "instance-captive-commander"
                            ),
                            "where": {
                                "eq": [
                                    "state.character.captive",
                                    True,
                                ],
                            },
                            "fields": {
                                "name": "a freed commander",
                                "attackable": True,
                            },
                            "state": {
                                "captive": False,
                            },
                        },
                    ],
                },
            ],
        )

        self._dispatch(self.player.id, "free commander")

        local_commander.refresh_from_db()
        parallel_commander.refresh_from_db()
        self.assertEqual(local_commander.name, "a freed commander")
        self.assertTrue(local_commander.attackable)
        self.assertFalse(
            get_state_snapshot(
                STATE_SCOPE_CHARACTER,
                local_commander,
            )["captive"]
        )
        self.assertEqual(
            parallel_commander.name,
            "a captive commander",
        )
        self.assertFalse(parallel_commander.attackable)
        self.assertTrue(
            get_state_snapshot(
                STATE_SCOPE_CHARACTER,
                parallel_commander,
            )["captive"]
        )
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.runtime_world_id, local_runtime.id)
        self.assertEqual(
            run.steps[0]["actions"][0]["mob_definition_id"],
            commander_definition.id,
        )

    def test_mob_typed_event_uses_event_actor_as_trigger_actor(self):
        import spawns.handlers  # noqa: F401
        from spawns.triggers import execute_mob_event_triggers

        mob_definition = MobDefinition.objects.create(
            world=self.world,
            name="Barley Keeper",
        )
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=mob_definition,
            name="Barley Keeper",
        )
        seed_item = self.seed.spawn(self.player, self.spawn_world)
        trigger = Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(Mob),
            target_id=mob.id,
            name="Keeper plants barley",
            event=adv_consts.MOB_REACTION_EVENT_SAYING,
            match="grow barley",
            steps=self._steps(),
            conditions=self._inventory_condition(),
            gate_delay=0,
            display_action_in_room=False,
        )

        with self.captureOnCommitCallbacks(execute=True):
            execute_mob_event_triggers(
                event=adv_consts.MOB_REACTION_EVENT_SAYING,
                actor=self.player,
                room=self.room,
                match_text="please grow barley",
                isolate_runtime_world=True,
                target_mob_id=mob.id,
            )

        self.assertFalse(Item.objects.filter(pk=seed_item.id).exists())
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.actor_type, "player")
        self.assertEqual(run.actor_id, self.player.id)

    def test_forced_say_preserves_depth_without_reentering_reaction(self):
        import spawns.handlers  # noqa: F401

        mob_definition = MobDefinition.objects.create(
            world=self.world,
            name="Echoing Keeper",
        )
        mob = Mob.objects.create(
            world=self.spawn_world,
            room=self.room,
            definition=mob_definition,
            name="Echoing Keeper",
        )
        trigger = Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(Mob),
            target_id=mob.id,
            name="Echo the player",
            event=adv_consts.MOB_REACTION_EVENT_SAYING,
            match="repeat forever",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "say repeat forever",
                        },
                    ],
                },
            ],
            gate_delay=0,
            display_action_in_room=False,
        )
        initial_depth = MAX_SCRIPT_COMMAND_DEPTH - 1
        event = GameEvent(
            type="cmd.say.success",
            recipients=[self.player.key],
            data={
                "actor": {
                    "key": self.player.key,
                    "name": self.player.name,
                },
                "text": "repeat forever",
                SCRIPT_COMMAND_DEPTH_KEY: initial_depth,
            },
        )

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                publish_events([event], actor_key=self.player.key)

        runs = list(ScheduledTriggerRun.objects.filter(trigger=trigger))
        self.assertEqual(len(runs), 1)
        self.assertEqual(
            runs[0].bindings[SCRIPT_COMMAND_DEPTH_KEY],
            initial_depth,
        )
        self.assertEqual(
            runs[0].status,
            ScheduledTriggerRun.STATUS_COMPLETED,
        )
        self.assertFalse(GameEventOutbox.objects.exists())
        forced_messages = [
            entry["message"]
            for entry in messages
            if (
                entry["message"]["type"] == "cmd.say.success"
                and entry["message"].get("text")
            )
        ]
        self.assertEqual(len(forced_messages), 1)
        self.assertNotIn(
            SCRIPT_COMMAND_PROVENANCE_KEY,
            forced_messages[0]["data"],
        )

    def test_say_event_step_trigger_ignores_same_room_mob_in_other_instance(self):
        fixture = self._cross_instance_mob_event_fixture(
            event=adv_consts.MOB_REACTION_EVENT_SAYING,
        )
        self.assertEqual(
            fixture.actor_runtime_mob.room_id,
            fixture.other_runtime_mob.room_id,
        )
        self.assertNotEqual(
            fixture.actor_runtime_mob.world_id,
            fixture.other_runtime_mob.world_id,
        )

        with patch("spawns.trigger_steps.start_trigger_steps") as mock_start:
            dispatch_text_command(self.player.id, "say please grow barley")

        mock_start.assert_called_once()
        call_kwargs = mock_start.call_args.kwargs
        self.assertEqual(call_kwargs["trigger"].id, fixture.trigger.id)
        self.assertEqual(call_kwargs["actor"].id, self.player.id)
        self.assertEqual(call_kwargs["room"].id, fixture.event_room.id)
        self.assertEqual(
            call_kwargs["gate_scope_key"],
            (
                f"runtime:{fixture.actor_runtime.id}:"
                f"mob:{fixture.actor_runtime_mob.id}"
            ),
        )

    def test_enter_event_step_trigger_ignores_same_room_mob_in_other_instance(self):
        fixture = self._cross_instance_mob_event_fixture(
            event=adv_consts.MOB_REACTION_EVENT_ENTERING,
            enter_event=True,
        )
        self.assertEqual(
            fixture.actor_runtime_mob.room_id,
            fixture.other_runtime_mob.room_id,
        )
        self.assertNotEqual(
            fixture.actor_runtime_mob.world_id,
            fixture.other_runtime_mob.world_id,
        )

        with patch("spawns.trigger_steps.start_trigger_steps") as mock_start:
            dispatch_text_command(self.player.id, "north")

        self.player.refresh_from_db()
        self.assertEqual(self.player.room_id, fixture.event_room.id)
        mock_start.assert_called_once()
        call_kwargs = mock_start.call_args.kwargs
        self.assertEqual(call_kwargs["trigger"].id, fixture.trigger.id)
        self.assertEqual(call_kwargs["actor"].id, self.player.id)
        self.assertEqual(call_kwargs["room"].id, fixture.event_room.id)
        self.assertEqual(
            call_kwargs["gate_scope_key"],
            (
                f"runtime:{fixture.actor_runtime.id}:"
                f"mob:{fixture.actor_runtime_mob.id}"
            ),
        )

    def test_poison_run_cancels_without_starving_next_due_run(self):
        second_player = self.create_player(
            "Second Farmer",
            user=self.create_user("poison-second-farmer@example.com"),
        )
        second_player.in_game = True
        second_player.save(update_fields=["in_game"])
        self.seed.spawn(self.player, self.spawn_world)
        self.seed.spawn(second_player, self.spawn_world)
        trigger = self._create_trigger(conditions=self._inventory_condition())
        self._dispatch(self.player.id)
        self._dispatch(second_player.id)
        poison_run, healthy_run = list(
            ScheduledTriggerRun.objects.filter(trigger=trigger).order_by("started_ts", "id")
        )
        poison_steps = deepcopy(poison_run.steps)
        poison_steps[1]["actions"] = [None]
        poison_run.steps = poison_steps
        poison_run.save(update_fields=["steps"])
        due_at = max(poison_run.next_run_ts, healthy_run.next_run_ts)

        with self.captureOnCommitCallbacks(execute=True):
            result = process_due_trigger_runs(limit=10, now=due_at)

        self.assertEqual(result["processed"], 2)
        self.assertEqual(result["cancelled"], 1)
        poison_run.refresh_from_db()
        healthy_run.refresh_from_db()
        self.assertEqual(poison_run.status, ScheduledTriggerRun.STATUS_CANCELLED)
        self.assertEqual(poison_run.failure_code, "step_exception")
        self.assertEqual(healthy_run.status, ScheduledTriggerRun.STATUS_ACTIVE)
        self.assertEqual(healthy_run.next_step_index, 2)
        self.assertTrue(Item.objects.filter(pk=healthy_run.bindings["crop"]["id"]).exists())

    def test_retryable_database_error_leaves_due_run_active(self):
        self.seed.spawn(self.player, self.spawn_world)
        trigger = self._create_trigger()
        self._dispatch(self.player.id)
        run = ScheduledTriggerRun.objects.get(trigger=trigger)

        with patch(
            "spawns.trigger_steps._execute_current_step",
            side_effect=OperationalError("retry this transaction"),
        ):
            with self.assertRaises(OperationalError):
                process_due_trigger_runs(now=run.next_run_ts)

        run.refresh_from_db()
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_ACTIVE)
        self.assertEqual(run.next_step_index, 1)

    def test_gate_releases_after_failed_step_and_atomically_blocks_next_start(self):
        steps = [self._steps()[0]]
        steps[0]["actions"][0]["count"] = 2
        trigger = self._create_trigger(
            steps=steps,
            conditions=self._inventory_condition(),
            gate_delay=60,
        )
        gate_key = (
            f"spawns.trigger_gate.{trigger.id}.runtime:{self.spawn_world.id}:"
            f"room:{self.room.id}"
        )
        self.addCleanup(cache.delete, gate_key)
        first_seed = self.seed.spawn(self.player, self.spawn_world)

        self._dispatch(self.player.id)

        self.assertTrue(Item.objects.filter(pk=first_seed.id).exists())
        self.assertFalse(ScheduledTriggerRun.objects.exists())
        self.assertIsNone(cache.get(gate_key))

        self.seed.spawn(self.player, self.spawn_world)
        self._dispatch(self.player.id)
        self.assertEqual(ScheduledTriggerRun.objects.count(), 1)
        self.assertEqual(
            ScheduledTriggerRun.objects.get().status,
            ScheduledTriggerRun.STATUS_COMPLETED,
        )
        self.assertTrue(cache.get(gate_key))

        self.seed.spawn(self.player, self.spawn_world)
        self.seed.spawn(self.player, self.spawn_world)
        self._dispatch(self.player.id)
        self.assertEqual(ScheduledTriggerRun.objects.count(), 1)
        self.assertEqual(
            self.player.inventory.filter(definition=self.seed).count(),
            2,
        )

    def test_same_actor_cannot_start_duplicate_active_run(self):
        self.seed.spawn(self.player, self.spawn_world)
        second_seed = self.seed.spawn(self.player, self.spawn_world)
        self._create_trigger(conditions=self._inventory_condition())

        self._dispatch(self.player.id)
        self._dispatch(self.player.id)

        self.assertEqual(ScheduledTriggerRun.objects.count(), 1)
        self.assertTrue(Item.objects.filter(pk=second_seed.id).exists())

    def test_actor_cannot_exceed_active_sequence_limit(self):
        now = timezone.now()
        ScheduledTriggerRun.objects.bulk_create([
            ScheduledTriggerRun(
                trigger=None,
                runtime_world=self.spawn_world,
                room=self.room,
                actor_type="player",
                actor_id=self.player.id,
                actor_key=self.player.key,
                steps=[],
                next_run_ts=now + timedelta(days=1),
                started_ts=now,
                status=ScheduledTriggerRun.STATUS_ACTIVE,
            )
            for _ in range(MAX_ACTIVE_TRIGGER_RUNS_PER_ACTOR)
        ])
        seed = self.seed.spawn(self.player, self.spawn_world)
        trigger = self._create_trigger(conditions=self._inventory_condition())

        result = start_trigger_steps(
            trigger=trigger,
            actor=self.player,
            room=self.room,
        )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "too_many_active_sequences")
        self.assertTrue(Item.objects.filter(pk=seed.id).exists())
        self.assertFalse(ScheduledTriggerRun.objects.filter(trigger=trigger).exists())

    def test_total_sequence_duration_is_capped_at_one_year(self):
        steps = [
            {
                "after_seconds": 0,
                "actions": [
                    {"type": "echo", "room": "trigger_room", "text": "Start"},
                ],
            },
            {
                "after_seconds": 31_536_000,
                "actions": [
                    {"type": "echo", "room": "trigger_room", "text": "Year"},
                ],
            },
            {
                "after_seconds": 1,
                "actions": [
                    {"type": "echo", "room": "trigger_room", "text": "Too late"},
                ],
            },
        ]

        with self.assertRaises(TriggerStepSpecError):
            normalize_trigger_steps(steps)

    def test_terminal_run_pruning_is_bounded_by_batch_limits(self):
        trigger = self._create_trigger(conditions="")
        old_ts = timezone.now() - timedelta(days=30)
        for actor_id in (self.player.id, self.player.id + 1):
            ScheduledTriggerRun.objects.create(
                trigger=trigger,
                runtime_world=self.spawn_world,
                room=self.room,
                actor_type="player",
                actor_id=actor_id,
                actor_key=f"player.{actor_id}",
                steps=[],
                next_run_ts=old_ts,
                started_ts=old_ts,
                status=ScheduledTriggerRun.STATUS_COMPLETED,
                completed_ts=old_ts,
            )
        ScheduledTriggerRun.objects.update(modified_ts=old_ts)

        deleted = prune_terminal_trigger_runs(
            retention_days=7,
            batch_size=1,
            max_batches=1,
        )

        self.assertEqual(deleted, 1)
        self.assertEqual(ScheduledTriggerRun.objects.count(), 1)

    def test_stale_deleted_cached_trigger_does_not_create_a_run(self):
        trigger = self._create_trigger(conditions="")
        stale_hook = SimpleNamespace(id=trigger.id)
        trigger.delete()

        result = start_trigger_steps(
            trigger=stale_hook,
            actor=self.player,
            room=self.room,
        )

        self.assertFalse(result.started)
        self.assertEqual(result.code, "trigger_missing")
        self.assertFalse(ScheduledTriggerRun.objects.exists())

    def test_after_move_exit_keeps_trigger_room_after_actor_moves(self):
        import spawns.handlers  # noqa: F401
        from spawns.triggers import execute_room_event_triggers

        destination = self.room.create_at("north")
        self.player.room = destination
        self.player.save(update_fields=["room"])
        trigger = Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            name="Barley left behind",
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_EXIT,
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "spawn_room_item",
                            "room": "trigger_room",
                            "item": "itemdefinition.barley-seedling",
                        },
                    ],
                },
            ],
            gate_delay=0,
            display_action_in_room=False,
        )

        with capture_game_messages() as messages:
            with self.captureOnCommitCallbacks(execute=True):
                execute_room_event_triggers(
                    event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_EXIT,
                    actor=self.player,
                    room=self.room,
                    origin_room_id=self.room.id,
                    destination_room_id=destination.id,
                    direction="north",
                )

        self.assertEqual(self._room_items(self.seedling).count(), 1)
        self.assertTrue(ScheduledTriggerRun.objects.filter(trigger=trigger).exists())
        self.assertTrue(
            any(
                entry["player_key"] == self.player.key
                and entry["message"]["type"]
                == "notification.trigger.items_changed"
                for entry in messages
            )
        )

    def test_room_event_trigger_can_start_typed_steps(self):
        import spawns.handlers  # noqa: F401
        from spawns.triggers import execute_room_event_triggers

        trigger = Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            name="Barley appears on entry",
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "spawn_room_item",
                            "room": "trigger_room",
                            "item": "itemdefinition.barley-seedling",
                        },
                    ],
                },
            ],
            gate_delay=0,
            display_action_in_room=False,
        )

        execute_room_event_triggers(
            event=adv_consts.TRIGGER_EVENT_AFTER_MOVE_ENTER,
            actor=self.player,
            room=self.room,
            origin_room_id=self.room.id,
            destination_room_id=self.room.id,
        )

        self.assertEqual(self._room_items(self.seedling).count(), 1)
        run = ScheduledTriggerRun.objects.get(trigger=trigger)
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)

    def test_in_flight_run_uses_snapshot_after_trigger_is_deleted(self):
        self.seed.spawn(self.player, self.spawn_world)
        trigger = self._create_trigger()
        self._dispatch(self.player.id)
        run = ScheduledTriggerRun.objects.get(trigger=trigger)

        trigger.delete()
        run.refresh_from_db()
        self.assertIsNone(run.trigger_id)

        result = process_due_trigger_runs(
            now=run.started_ts + timedelta(seconds=20),
        )

        self.assertEqual(result["processed"], 1)
        self.assertEqual(self._room_items(self.growing).count(), 1)

    def test_deleted_trigger_identity_survives_in_command_provenance(self):
        trigger = self._create_trigger(
            match="begin vanishing script",
            conditions="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The sequence begins.",
                        },
                    ],
                },
                {
                    "after_seconds": 1,
                    "actions": [
                        {
                            "type": "command",
                            "subject": "trigger_actor",
                            "command": "say The source is gone.",
                        },
                    ],
                },
            ],
        )
        trigger_id = trigger.id
        trigger_key = trigger.key
        self._dispatch(self.player.id, "begin vanishing script")
        run = ScheduledTriggerRun.objects.get(trigger=trigger)

        trigger.delete()
        run.refresh_from_db()
        self.assertIsNone(run.trigger_id)

        result = process_due_trigger_runs(
            now=run.started_ts + timedelta(seconds=1),
        )

        self.assertEqual(result["processed"], 1)
        say_event = GameEventOutbox.objects.get(
            event_type="cmd.say.success",
        )
        provenance = say_event.data[SCRIPT_COMMAND_PROVENANCE_KEY]
        self.assertEqual(provenance["trigger_id"], trigger_id)
        self.assertEqual(provenance["trigger_key"], trigger_key)


class TestConcurrentHarvestTriggerSteps(TransactionTestCase):
    def setUp(self):
        super().setUp()
        user_model = get_user_model()
        self.first_user = user_model.objects.create_user(
            "first-harvester@example.com",
            "p",
        )
        self.second_user = user_model.objects.create_user(
            "second-harvester@example.com",
            "p",
        )
        config = WorldConfig.objects.create()
        self.authored_world = World.objects.new_world(
            name="Concurrent Barley Field",
            author=self.first_user,
            config=config,
        )
        self.runtime_world = self.authored_world.create_spawn_world()
        self.room = self.authored_world.zones.first().rooms.first()
        self.first_player = self._create_player(
            user=self.first_user,
            name="First Harvester",
        )
        self.second_player = self._create_player(
            user=self.second_user,
            name="Second Harvester",
        )
        self.mature = ItemDefinition.objects.create(
            world=self.authored_world,
            slug="barley-mature",
            name="a bunch of mature barley plants",
        )
        self.harvested = ItemDefinition.objects.create(
            world=self.authored_world,
            slug="harvested-barley",
            name="a bunch of harvested barley",
        )
        self.trigger = Trigger.objects.create(
            world=self.authored_world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            name="Harvest barley",
            match="harvest barley",
            script="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "consume_room_item",
                            "room": "trigger_room",
                            "item": "itemdefinition.barley-mature",
                        },
                        {
                            "type": "grant_item",
                            "actor": "trigger_actor",
                            "item": "itemdefinition.harvested-barley",
                        },
                    ],
                },
            ],
            conditions=json.dumps({
                "item_present": {
                    "location": "room",
                    "item": "itemdefinition.barley-mature",
                },
            }),
            display_action_in_room=True,
        )
        self.mature.spawn(self.room, self.runtime_world)

    def _create_player(self, *, user, name):
        return Player.objects.create(
            user=user,
            name=name,
            room=self.room,
            world=self.runtime_world,
            in_game=True,
        )

    def test_two_players_cannot_harvest_the_same_crop(self):
        barrier = Barrier(2)

        def harvest_once(player_id):
            close_old_connections()
            try:
                actor = Player.objects.get(pk=player_id)
                room = Room.objects.get(pk=self.room.id)
                trigger = Trigger.objects.get(pk=self.trigger.id)
                barrier.wait(timeout=5)
                result = start_trigger_steps(
                    trigger=trigger,
                    actor=actor,
                    room=room,
                )
                return "started" if result.started else result.code
            finally:
                close_old_connections()

        with patch("spawns.trigger_steps._flush_queued_events"):
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(
                    harvest_once,
                    [self.first_player.id, self.second_player.id],
                ))

        self.assertEqual(sorted(outcomes), ["conditions_failed", "started"])
        self.assertFalse(
            Item.objects.filter(
                world=self.runtime_world,
                definition=self.mature,
            ).exists()
        )
        self.assertEqual(
            Item.objects.filter(
                world=self.runtime_world,
                definition=self.harvested,
            ).count(),
            1,
        )
        self.assertEqual(
            ScheduledTriggerRun.objects.filter(trigger=self.trigger).count(),
            1,
        )

    def test_concurrent_same_room_starts_cannot_overspend_one_player(self):
        obol = create_currency(
            world=self.authored_world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.first_player,
            {obol: 10},
            reason="test.setup",
            emit_event=False,
        )
        toll_trigger = Trigger.objects.create(
            world=self.authored_world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            name="Pay concurrent toll",
            match="pay toll",
            script="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "debit_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
            display_action_in_room=True,
        )
        barrier = Barrier(2)

        def debit_once(_attempt):
            close_old_connections()
            try:
                actor = Player.objects.get(pk=self.first_player.id)
                room = Room.objects.get(pk=self.room.id)
                trigger = Trigger.objects.get(pk=toll_trigger.id)
                barrier.wait(timeout=5)
                result = start_trigger_steps(
                    trigger=trigger,
                    actor=actor,
                    room=room,
                )
                return "started" if result.started else result.code
            finally:
                close_old_connections()

        with patch("spawns.trigger_steps._flush_queued_events"):
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(debit_once, range(2)))

        self.assertEqual(
            sorted(outcomes),
            ["insufficient_funds", "started"],
        )
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.first_player,
                currency=obol,
            ).amount,
            0,
        )
        self.assertEqual(
            ScheduledTriggerRun.objects.filter(trigger=toll_trigger).count(),
            1,
        )

    def test_concurrent_currency_grants_cannot_exceed_maximum_balance(self):
        obol = create_currency(
            world=self.authored_world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.first_player,
            {obol: MAX_CURRENCY_AMOUNT - 10},
            reason="test.setup",
            emit_event=False,
        )
        revision_before = self.first_player.wallet_revision
        grant_trigger = Trigger.objects.create(
            world=self.authored_world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            name="Claim concurrent stipend",
            match="claim stipend",
            script="",
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "grant_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 10,
                        },
                    ],
                },
            ],
            display_action_in_room=True,
        )
        barrier = Barrier(2)

        def grant_once(_attempt):
            close_old_connections()
            try:
                actor = Player.objects.get(pk=self.first_player.id)
                room = Room.objects.get(pk=self.room.id)
                trigger = Trigger.objects.get(pk=grant_trigger.id)
                barrier.wait(timeout=5)
                result = start_trigger_steps(
                    trigger=trigger,
                    actor=actor,
                    room=room,
                )
                return "started" if result.started else result.code
            finally:
                close_old_connections()

        with patch("spawns.trigger_steps._flush_queued_events"):
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(grant_once, range(2)))

        self.assertEqual(
            sorted(outcomes),
            ["amount_out_of_range", "started"],
        )
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.first_player,
                currency=obol,
            ).amount,
            MAX_CURRENCY_AMOUNT,
        )
        self.first_player.refresh_from_db()
        self.assertEqual(
            self.first_player.wallet_revision,
            revision_before + 1,
        )
        self.assertEqual(
            ScheduledTriggerRun.objects.filter(trigger=grant_trigger).count(),
            1,
        )
        self.assertEqual(
            GameEventOutbox.objects.filter(
                event_type="currency.balances_changed",
            ).count(),
            1,
        )
        self.assertEqual(
            GameEventOutbox.objects.filter(
                event_type="notification.trigger.currency_granted",
            ).count(),
            2,
        )

    def test_concurrent_due_runs_contend_safely_on_one_player_wallet(self):
        obol = create_currency(
            world=self.authored_world,
            code="obol",
            name="obol",
            plural_name="obols",
        )
        mutate_balances(
            self.first_player,
            {obol: 10},
            reason="test.setup",
            emit_event=False,
        )
        revision_before = self.first_player.wallet_revision
        second_room = self.room.create_at("north")

        def create_toll_trigger(*, room, match):
            return Trigger.objects.create(
                world=self.authored_world,
                scope=adv_consts.TRIGGER_SCOPE_ROOM,
                kind=adv_consts.TRIGGER_KIND_COMMAND,
                target_type=ContentType.objects.get_for_model(Room),
                target_id=room.id,
                name=f"Delayed {match}",
                match=match,
                script="",
                steps=[
                    {
                        "after_seconds": 0,
                        "actions": [
                            {
                                "type": "echo",
                                "room": "trigger_room",
                                "text": "A toll is pending.",
                            },
                        ],
                    },
                    {
                        "after_seconds": 1,
                        "actions": [
                            {
                                "type": "debit_currency",
                                "actor": "trigger_actor",
                                "currency": "obol",
                                "amount": 10,
                            },
                        ],
                    },
                ],
                display_action_in_room=True,
            )

        first_trigger = create_toll_trigger(
            room=self.room,
            match="first delayed toll",
        )
        second_trigger = create_toll_trigger(
            room=second_room,
            match="second delayed toll",
        )
        with patch("spawns.trigger_steps._flush_queued_events"):
            first_start = start_trigger_steps(
                trigger=first_trigger,
                actor=self.first_player,
                room=self.room,
            )
            self.assertTrue(first_start.started)
            self.first_player.room = second_room
            self.first_player.save(update_fields=["room"])
            second_start = start_trigger_steps(
                trigger=second_trigger,
                actor=self.first_player,
                room=second_room,
            )
            self.assertTrue(second_start.started)
        runs = list(
            ScheduledTriggerRun.objects.filter(
                trigger__in=[first_trigger, second_trigger],
            ).order_by("id")
        )
        due_at = max(run.next_run_ts for run in runs)
        GameEventOutbox.objects.all().delete()
        barrier = Barrier(2)

        def advance_once(_attempt):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return process_due_trigger_runs(limit=1, now=due_at)
            finally:
                close_old_connections()

        with patch("spawns.trigger_steps._flush_queued_events"):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(advance_once, range(2)))

        self.assertEqual(
            sum(result["processed"] for result in results),
            2,
        )
        self.assertEqual(
            sum(result["completed"] for result in results),
            1,
        )
        self.assertEqual(
            sum(result["cancelled"] for result in results),
            1,
        )
        self.assertEqual(
            list(
                ScheduledTriggerRun.objects.filter(pk__in=[run.pk for run in runs])
                .order_by("status")
                .values_list("status", "failure_code")
            ),
            [
                (ScheduledTriggerRun.STATUS_CANCELLED, "insufficient_funds"),
                (ScheduledTriggerRun.STATUS_COMPLETED, ""),
            ],
        )
        self.assertEqual(
            PlayerCurrencyBalance.objects.get(
                player=self.first_player,
                currency=obol,
            ).amount,
            0,
        )
        self.first_player.refresh_from_db()
        self.assertEqual(
            self.first_player.wallet_revision,
            revision_before + 1,
        )
        self.assertEqual(
            GameEventOutbox.objects.filter(
                event_type="currency.balances_changed",
            ).count(),
            1,
        )
        self.assertEqual(
            GameEventOutbox.objects.filter(
                event_type="notification.trigger.currency_debited",
            ).count(),
            1,
        )

    def test_duplicate_specific_continuations_advance_one_cursor_once(self):
        continuation_trigger = Trigger.objects.create(
            world=self.authored_world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.room.id,
            name="Concurrent continuation",
            match="ring delayed bell",
            script="",
            steps=[
                {
                    "after_seconds": 1,
                    "actions": [
                        {
                            "type": "echo",
                            "room": "trigger_room",
                            "text": "The delayed bell rings.",
                        },
                    ],
                },
            ],
            conditions="",
            display_action_in_room=True,
        )
        with patch("spawns.trigger_steps._flush_queued_events"):
            start = start_trigger_steps(
                trigger=continuation_trigger,
                actor=self.first_player,
                room=self.room,
            )
        run = ScheduledTriggerRun.objects.get(pk=start.run_id)
        barrier = Barrier(2)

        def continue_once(_attempt):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return advance_due_trigger_run(
                    run_id=run.id,
                    expected_step_index=0,
                    now=run.next_run_ts,
                )
            finally:
                close_old_connections()

        with patch("spawns.trigger_steps._flush_queued_events"):
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(continue_once, range(2)))

        self.assertEqual(
            outcomes.count(ScheduledTriggerRun.STATUS_COMPLETED),
            1,
        )
        self.assertEqual(outcomes.count(None), 1)
        run.refresh_from_db()
        self.assertEqual(run.status, ScheduledTriggerRun.STATUS_COMPLETED)
        self.assertEqual(run.next_step_index, 1)
        self.assertEqual(
            GameEventOutbox.objects.filter(
                event_type="notification./echo",
            ).count(),
            1,
        )
