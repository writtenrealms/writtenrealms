import json
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

from builders.models import ItemDefinition, MobDefinition, Trigger
from config import constants as adv_consts
from core.trigger_steps import TriggerStepSpecError, normalize_trigger_steps
from spawns.models import GameEventOutbox, Item, Mob, Player, ScheduledTriggerRun
from spawns.trigger_steps import (
    MAX_ACTIVE_TRIGGER_RUNS_PER_ACTOR,
    process_due_trigger_runs,
    prune_terminal_trigger_runs,
    start_trigger_steps,
)
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
        steps[1]["actions"].append({
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
                        "type": "echo",
                        "room": "trigger_room",
                        "text": "This echo must roll back.",
                    },
                    {
                        "type": "consume_item",
                        "actor": "trigger_actor",
                        "item": "itemdefinition.barley-seed",
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
