import json

from django.contrib.contenttypes.models import ContentType

import yaml

from rest_framework import serializers
from rest_framework.reverse import reverse

from builders import world_export as builder_world_export
from builders.currencies import create_currency, replace_starting_balances
from builders.models import (
    BuilderAssignment,
    Currency,
    ItemDefinition,
    MobDefinition,
    Path,
    PathRoom,
    Trigger,
    WorldBuilder,
)
from config import constants as adv_consts
from core.scoped_state import (
    STATE_SCOPE_ROOM,
    STATE_SCOPE_WORLD,
    STATE_SCOPE_ZONE,
    replace_initial_state_snapshot,
)
from quests.models import QuestArcTemplate, QuestTemplate
from tests.base import WorldTestCase
from worlds.models import (
    Door,
    Doorway,
    Room,
    RoomDetail,
    RoomFlag,
    WorldConfig,
    Zone,
)


class AuthenticatedBuilderWorldTestCase(WorldTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.user)


class TestWorldExportImport(AuthenticatedBuilderWorldTestCase):
    def setUp(self):
        super().setUp()
        self.export_ep = reverse("builder-world-export", args=[self.world.pk])
        self.apply_ep = reverse("builder-world-manifest-apply", args=[self.world.pk])
        self._build_source_world()

    def _build_source_world(self):
        self.obol = create_currency(
            world=self.world,
            code="obol",
            name="Obol",
            plural_name="Obols",
        )
        self.world.config.death_currency = self.obol
        self.world.config.clan_registration_currency = self.obol
        self.world.config.save(update_fields=[
            "death_currency",
            "clan_registration_currency",
        ])
        self.world.name = "Export Source"
        self.world.short_description = "Canonical export test"
        self.world.description = "World export/import should round-trip."
        self.world.motd = "Welcome to the harbor."
        self.world.is_public = True
        self.world.save()
        replace_initial_state_snapshot(
            STATE_SCOPE_WORLD,
            self.world,
            {"weather": "windy"},
        )

        self.start_room = self.room
        self.start_zone = self.zone
        self.start_zone.name = "Old Quarter"
        self.start_zone.description = "The original quarter."
        self.start_zone.notes = "Legacy district."
        self.start_zone.pvp_zone = True
        self.start_zone.save()

        self.start_room.name = "Old Gate"
        self.start_room.description = "An old gate opens toward the harbor."
        self.start_room.note = "The stone is worn smooth."
        self.start_room.color = "#998877"
        self.start_room.save()
        replace_initial_state_snapshot(
            STATE_SCOPE_ROOM,
            self.start_room,
            {"gate_open": False},
        )

        RoomFlag.objects.create(room=self.start_room, code=adv_consts.ROOM_FLAG_DARK)
        RoomDetail.objects.create(
            room=self.start_room,
            keywords="inscription",
            description="A weathered inscription details the harbor laws.",
            is_hidden=False,
        )

        self.harbor_zone = Zone.objects.create(
            world=self.world,
            name="Harbor District",
            description="Docks and trade routes.",
            notes="Primary arrival zone.",
            respawn_wait=120,
            pvp_zone=False,
        )
        replace_initial_state_snapshot(
            STATE_SCOPE_ZONE,
            self.harbor_zone,
            {"fog_level": 2, "harbor_weather": "windy"},
        )
        self.harbor_room = Room.objects.create(
            world=self.world,
            zone=self.harbor_zone,
            name="Harbor Square",
            description="Ships crowd the waterline.",
            note="Vendors shout over gulls.",
            x=10,
            y=0,
            z=0,
        )
        self.start_room.east = self.harbor_room
        self.start_room.save(update_fields=["east"])
        self.harbor_zone.center = self.harbor_room
        self.harbor_zone.save(update_fields=["center"])

        self.patrol_path = Path.objects.create(
            world=self.world,
            zone=self.harbor_zone,
            name="Patrol Loop",
            notes="Harbor guard patrol route.",
            entry_room=self.harbor_room,
            max_per_room=2,
            max_per_path=5,
        )
        PathRoom.objects.create(path=self.patrol_path, room=self.start_room)
        PathRoom.objects.create(path=self.patrol_path, room=self.harbor_room)

        self.world.config.starting_room = self.harbor_room
        self.world.config.death_room = self.harbor_room
        self.world.config.built_by = "WR Export Tests"
        self.world.config.small_background = "https://assets.example/small.png"
        self.world.config.large_background = "https://assets.example/large.png"
        self.world.config.name_exclusions = "admin\nmoderator"
        self.world.config.save()
        replace_starting_balances(
            world=self.world,
            balances={self.obol: 12},
        )

        self.marks = create_currency(
            world=self.world,
            code="marks",
            name="Marks",
        )

        self.brass_key = ItemDefinition.objects.create(
            world=self.world,
            slug="brass_key",
            name="a brass key",
        )
        self.lockbox = ItemDefinition.objects.create(
            world=self.world,
            slug="lockbox",
            name="a lockbox",
            item_type=adv_consts.ITEM_TYPE_CONTAINER,
            base_properties={"capacity": 10},
            notes="Used in the harbor office.",
        )
        self.barley_seed = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seed",
            name="a barley seed",
        )
        self.barley_seedling = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-seedling",
            name="a barley seedling",
        )
        self.barley_growing = ItemDefinition.objects.create(
            world=self.world,
            slug="barley-growing",
            name="a growing barley plant",
        )

        doorway = Doorway.objects.create(
            world=self.world,
            key=self.brass_key,
            destroy_key=False,
            default_state=adv_consts.DOOR_STATE_CLOSED,
        )
        Door.objects.create(
            doorway=doorway,
            direction="east",
            from_room=self.start_room,
            to_room=self.harbor_room,
            name="harbor gate",
        )

        self.quartermaster = MobDefinition.objects.create(
            world=self.world,
            slug="quartermaster",
            name="Quartermaster",
            mob_type=adv_consts.MOB_TYPE_HUMANOID,
            description="Keeps the harbor ledgers.",
            base_properties={"level": 4},
        )

        self.quest_arc = QuestArcTemplate.objects.create(
            world=self.world,
            slug="harbor-work",
            name="Harbor Work",
            summary="Work that starts on the docks.",
        )
        self.quest = QuestTemplate.objects.create(
            world=self.world,
            arc=self.quest_arc,
            slug="harbor_delivery",
            name="Harbor Delivery",
            quest_type="quest",
            scope="player",
            status="active",
            repeatability_mode="never",
            repeatability_cooldown_seconds=0,
            max_active=1,
            discovery_policy={
                "sources": [
                    {
                        "type": "npc_dialogue",
                        "mob_definition": self.quartermaster.id,
                    }
                ],
                "visible_if": {},
                "accept_if": {},
                "salience": 80,
                "cooldown_seconds": 0,
            },
            slot_schema={},
            graph={
                "steps": [
                    {
                        "id": "deliver",
                        "kind": "objective",
                        "recap": "Deliver the brass key.",
                        "objectives": [
                            {
                                "id": "turn_in_key",
                                "text": "Hand the quartermaster the brass key.",
                                "tracker": {
                                    "event": "quest.item.delivered",
                                    "where": {
                                        "all": [
                                            {
                                                "eq": [
                                                    "event.target.definition_id",
                                                    self.quartermaster.id,
                                                ]
                                            },
                                            {
                                                "eq": [
                                                    "event.item.definition_id",
                                                    self.brass_key.id,
                                                ]
                                            },
                                        ]
                                    },
                                },
                                "progress": {
                                    "mode": "count",
                                    "target": 1,
                                },
                            }
                        ],
                        "transitions": [
                            {
                                "when": {"objective_complete": "turn_in_key"},
                                "goto": "resolved",
                            }
                        ],
                    },
                    {
                        "id": "resolved",
                        "kind": "resolution",
                        "recap": "The quartermaster receives the delivery.",
                    },
                ]
            },
            reward_policy={
                "complete": [
                    {
                        "type": "mob_command",
                        "mob_definition": self.quartermaster.id,
                        "command": "say Delivery received.",
                    }
                ],
                "compromised": [],
                "failed_forward": [],
                "expired": [],
            },
        )
        self.room_survey_quest = QuestTemplate.objects.create(
            world=self.world,
            arc=self.quest_arc,
            slug="harbor_survey",
            name="Harbor Survey",
            quest_type="quest",
            scope="player",
            status="active",
            repeatability_mode="never",
            repeatability_cooldown_seconds=0,
            max_active=1,
            discovery_policy={
                "sources": [
                    {
                        "type": "room_prompt",
                        "room": f"room.{self.start_room.id}",
                        "callout": "A harbor survey notice has been posted here.",
                    }
                ],
                "visible_if": {},
                "accept_if": {},
                "salience": 40,
                "cooldown_seconds": 0,
            },
            slot_schema={},
            graph={
                "steps": [
                    {
                        "id": "survey",
                        "kind": "objective",
                        "recap": "Inspect the old gate and the harbor square.",
                        "objectives": [
                            {
                                "id": "inspect_harbor",
                                "text": "Inspect both harbor rooms.",
                                "tracker": {
                                    "event": "cmd.look.success",
                                    "where": {
                                        "all": [
                                            {"eq": ["event.target_type", "room"]},
                                            {
                                                "in": [
                                                    "event.target.id",
                                                    [self.start_room.id, self.harbor_room.id],
                                                ]
                                            },
                                        ]
                                    },
                                },
                                "progress": {
                                    "mode": "unique_count",
                                    "target": 2,
                                    "distinct_by": "event.target.id",
                                },
                            }
                        ],
                        "transitions": [
                            {
                                "when": {"objective_complete": "inspect_harbor"},
                                "goto": "resolved",
                            }
                        ],
                    },
                    {
                        "id": "resolved",
                        "kind": "resolution",
                        "recap": "The harbor route has been checked.",
                    },
                ]
            },
            reward_policy={
                "complete": [],
                "compromised": [],
                "failed_forward": [],
                "expired": [],
            },
        )

        room_ct = ContentType.objects.get_for_model(Room)
        mob_ct = ContentType.objects.get_for_model(MobDefinition)
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=room_ct,
            target_id=self.start_room.id,
            name="Open Harbor Gate",
            match="open harbor gate",
            script="/cmd room -- /echo -- The gate unlocks.",
            conditions="",
            display_action_in_room=True,
            gate_delay=5,
            order=1,
            is_active=True,
        )
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=room_ct,
            target_id=self.start_room.id,
            name="Plant Barley",
            match="plant seed",
            script="",
            conditions=json.dumps({
                "item_present": {
                    "location": "actor_inventory",
                    "item": self.barley_seed.id,
                },
            }),
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "consume_item",
                            "actor": "trigger_actor",
                            "item": self.barley_seed.id,
                            "count": 1,
                        },
                        {
                            "type": "spawn_room_item",
                            "room": "trigger_room",
                            "item": self.barley_seedling.id,
                            "bind": "crop",
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
                                "mob": self.quartermaster.id,
                                "where": {
                                    "eq": [
                                        "state.character.on_duty",
                                        True,
                                    ],
                                },
                            },
                            "command": "emote nods approvingly.",
                        },
                    ],
                },
                {
                    "after_seconds": 20,
                    "actions": [
                        {
                            "type": "replace_room_item",
                            "target": "crop",
                            "with": self.barley_growing.id,
                        },
                    ],
                },
            ],
            on_step_error="cancel",
            display_action_in_room=True,
            gate_delay=0,
            order=3,
            is_active=True,
        )
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_EVENT,
            target_type=mob_ct,
            target_id=self.quartermaster.id,
            name="Quartermaster Greeting",
            event=adv_consts.MOB_REACTION_EVENT_SAYING,
            match="hello",
            script="say Welcome to the harbor office.",
            conditions="",
            display_action_in_room=False,
            gate_delay=3,
            order=2,
            is_active=True,
        )

    def test_world_export_endpoint_returns_multi_document_yaml(self):
        resp = self.client.get(self.export_ep)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("yaml", resp.data)
        exported_docs = [doc for doc in yaml.safe_load_all(resp.data["yaml"]) if doc is not None]
        self.assertEqual(resp.data["summary"]["documents"], len(exported_docs))
        self.assertEqual(resp.data["summary"]["rooms"], 2)
        self.assertEqual(resp.data["summary"]["zones"], 2)
        self.assertEqual(resp.data["summary"]["paths"], 1)
        self.assertEqual(exported_docs[-1]["kind"], "world")

        expected_kinds = (
            ["currency"] * resp.data["summary"]["currencies"]
            + ["itemdefinition"] * resp.data["summary"]["item_definitions"]
            + ["itembundle"] * resp.data["summary"]["item_bundles"]
            + ["merchantprofile"] * resp.data["summary"]["merchant_profiles"]
            + ["zone"] * resp.data["summary"]["zones"]
            + ["room"] * resp.data["summary"]["rooms"]
            + ["path"] * resp.data["summary"]["paths"]
            + ["mobdefinition"] * resp.data["summary"]["mob_definitions"]
            + ["spawnplan"] * resp.data["summary"]["spawn_plans"]
            + ["ability"] * resp.data["summary"]["abilities"]
            + ["questarc"] * resp.data["summary"]["quest_arcs"]
            + ["quest"] * resp.data["summary"]["quests"]
            + ["trigger"] * resp.data["summary"]["triggers"]
            + ["world"]
        )
        self.assertEqual([doc["kind"] for doc in exported_docs], expected_kinds)

        zone_docs = [doc for doc in exported_docs if doc["kind"] == "zone"]
        self.assertEqual(
            {doc["metadata"]["name"]: doc["metadata"]["ref"] for doc in zone_docs},
            {
                self.start_zone.name: f"zone@{self.start_zone.relative_id}",
                self.harbor_zone.name: f"zone@{self.harbor_zone.relative_id}",
            },
        )
        for zone_doc in zone_docs:
            self.assertNotIn("is_warzone", zone_doc["spec"])
        harbor_zone_doc = next(
            doc
            for doc in zone_docs
            if doc["metadata"]["name"] == self.harbor_zone.name
        )
        self.assertEqual(
            harbor_zone_doc["spec"]["initial_state"],
            {"fog_level": 2, "harbor_weather": "windy"},
        )
        room_doc = next(
            doc for doc in exported_docs
            if doc["kind"] == "room" and doc["metadata"]["ref"] == f"room@{self.harbor_room.x},{self.harbor_room.y},{self.harbor_room.z}"
        )
        self.assertEqual(room_doc["spec"]["zone"], f"zone@{self.harbor_zone.relative_id}")
        world_doc = exported_docs[-1]
        self.assertEqual(world_doc["spec"]["initial_state"], {"weather": "windy"})
        start_room_doc = next(
            doc
            for doc in exported_docs
            if doc["kind"] == "room"
            and doc["metadata"]["ref"]
            == f"room@{self.start_room.x},{self.start_room.y},{self.start_room.z}"
        )
        self.assertEqual(
            start_room_doc["spec"]["initial_state"],
            {"gate_open": False},
        )

        path_doc = next(doc for doc in exported_docs if doc["kind"] == "path")
        self.assertEqual(path_doc["metadata"]["ref"], f"path@{self.patrol_path.relative_id}")
        self.assertEqual(path_doc["metadata"]["name"], "Patrol Loop")
        self.assertEqual(path_doc["spec"]["zone"], f"zone@{self.harbor_zone.relative_id}")
        self.assertEqual(path_doc["spec"]["entry_room"], f"room@{self.harbor_room.x},{self.harbor_room.y},{self.harbor_room.z}")
        self.assertEqual(
            path_doc["spec"]["rooms"],
            [
                f"room@{self.start_room.x},{self.start_room.y},{self.start_room.z}",
                f"room@{self.harbor_room.x},{self.harbor_room.y},{self.harbor_room.z}",
            ],
        )

    def test_world_export_round_trips_into_fresh_world(self):
        self.maxDiff = None
        source_export_resp = self.client.get(self.export_ep)
        self.assertEqual(source_export_resp.status_code, 200)
        source_docs = [doc for doc in yaml.safe_load_all(source_export_resp.data["yaml"]) if doc is not None]

        target_config = WorldConfig.objects.create()
        target_world = self.world.__class__.objects.new_world(
            name="Import Target",
            author=self.user,
            config=target_config,
        )

        target_apply_ep = reverse("builder-world-manifest-apply", args=[target_world.pk])
        target_export_ep = reverse("builder-world-export", args=[target_world.pk])

        apply_resp = self.client.post(
            target_apply_ep,
            {"manifest": source_export_resp.data["yaml"]},
            format="json",
        )
        self.assertEqual(apply_resp.status_code, 200, apply_resp.data)
        self.assertEqual(apply_resp.data["kind"], "batch")
        self.assertEqual(apply_resp.data["operation"], "applied")
        self.assertEqual(apply_resp.data["summary"]["documents"], len(source_docs))

        target_export_resp = self.client.get(target_export_ep)
        self.assertEqual(target_export_resp.status_code, 200)
        target_docs = [doc for doc in yaml.safe_load_all(target_export_resp.data["yaml"]) if doc is not None]

        self.assertEqual(source_docs, target_docs)

    def test_batch_rejects_conflicting_reciprocal_door_settings(self):
        room_a = "room@0,0,0"
        room_b = "room@10,0,0"
        manifests = [
            {
                "kind": "room",
                "metadata": {"ref": room_a},
                "spec": {
                    "doors": [{
                        "direction": "east",
                        "to_room": room_b,
                        "key": "itemdefinition.brass_key",
                        "destroy_key": False,
                        "default_state": "closed",
                    }],
                },
            },
            {
                "kind": "room",
                "metadata": {"ref": room_b},
                "spec": {
                    "doors": [{
                        "direction": "west",
                        "to_room": room_a,
                        "key": "itemdefinition.brass_key",
                        "destroy_key": True,
                        "default_state": "locked",
                    }],
                },
            },
        ]

        with self.assertRaises(serializers.ValidationError):
            builder_world_export.validate_room_door_stream_consistency(
                manifests
            )

    def test_world_export_serializes_portable_room_refs_inside_quests(self):
        resp = self.client.get(self.export_ep)
        self.assertEqual(resp.status_code, 200)

        exported_docs = [doc for doc in yaml.safe_load_all(resp.data["yaml"]) if doc is not None]
        survey_manifest = next(doc for doc in exported_docs if doc["kind"] == "quest" and doc["metadata"]["slug"] == "harbor_survey")

        expected_start_ref = f"room@{self.start_room.x},{self.start_room.y},{self.start_room.z}"
        expected_harbor_ref = f"room@{self.harbor_room.x},{self.harbor_room.y},{self.harbor_room.z}"

        self.assertEqual(
            survey_manifest["spec"]["discovery"]["sources"][0]["room"],
            expected_start_ref,
        )
        tracker_conditions = survey_manifest["spec"]["steps"][0]["objectives"][0]["tracker"]["where"]["all"]
        self.assertEqual(
            tracker_conditions[1]["in"][1],
            [expected_start_ref, expected_harbor_ref],
        )

    def test_world_export_serializes_portable_refs_inside_trigger_steps(self):
        resp = self.client.get(self.export_ep)
        self.assertEqual(resp.status_code, 200)

        exported_docs = [
            doc for doc in yaml.safe_load_all(resp.data["yaml"])
            if doc is not None
        ]
        trigger = next(
            doc for doc in exported_docs
            if doc["kind"] == "trigger" and doc["metadata"]["name"] == "Plant Barley"
        )

        self.assertEqual(
            trigger["spec"]["conditions"]["item_present"]["item"],
            "itemdefinition.barley-seed",
        )
        self.assertEqual(
            trigger["spec"]["steps"][0]["actions"][0]["item"],
            "itemdefinition.barley-seed",
        )
        self.assertEqual(
            trigger["spec"]["steps"][0]["actions"][1]["item"],
            "itemdefinition.barley-seedling",
        )
        self.assertEqual(
            trigger["spec"]["steps"][0]["actions"][2]["currency"],
            "obol",
        )
        self.assertEqual(
            trigger["spec"]["steps"][0]["actions"][3]["subject"]["mob"],
            "mobdefinition.quartermaster",
        )
        self.assertEqual(
            trigger["spec"]["steps"][0]["actions"][3]["subject"]["where"],
            {
                "eq": [
                    "state.character.on_duty",
                    True,
                ],
            },
        )
        self.assertEqual(
            trigger["spec"]["steps"][1]["actions"][0]["with"],
            "itemdefinition.barley-growing",
        )

    def test_world_export_canonicalizes_refs_inside_mob_present_where(self):
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.start_room.id,
            name="Question Quartermaster",
            match="question quartermaster",
            conditions=json.dumps({
                "mob_present": {
                    "ref": self.quartermaster.id,
                    "where": {
                        "eq": [
                            "actor.definition_id",
                            self.quartermaster.id,
                        ],
                    },
                },
            }),
            display_action_in_room=True,
            is_active=True,
        )

        resp = self.client.get(self.export_ep)

        self.assertEqual(resp.status_code, 200, resp.data)
        exported_docs = [
            doc for doc in yaml.safe_load_all(resp.data["yaml"])
            if doc is not None
        ]
        trigger = next(
            doc for doc in exported_docs
            if doc["kind"] == "trigger"
            and doc["metadata"]["name"] == "Question Quartermaster"
        )
        self.assertEqual(
            trigger["spec"]["conditions"]["mob_present"],
            {
                "ref": "mobdefinition.quartermaster",
                "where": {
                    "eq": [
                        "actor.definition_id",
                        "mobdefinition.quartermaster",
                    ],
                },
            },
        )

    def test_world_export_preserves_typed_numeric_itemdefinition_slugs(self):
        numeric_slug = str(self.barley_seed.id)
        ItemDefinition.objects.create(
            world=self.world,
            slug=numeric_slug,
            name="a numbered seed",
        )
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.start_room.id,
            name="Plant Numbered Seed",
            match="plant numbered seed",
            script="",
            conditions=json.dumps({
                "item_present": {
                    "location": "actor_inventory",
                    "item": f"itemdefinition.{numeric_slug}",
                },
            }),
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "consume_item",
                            "actor": "trigger_actor",
                            "item": f"itemdefinition.{numeric_slug}",
                        },
                    ],
                },
            ],
            display_action_in_room=True,
            is_active=True,
        )

        resp = self.client.get(self.export_ep)

        self.assertEqual(resp.status_code, 200, resp.data)
        exported_docs = [
            doc for doc in yaml.safe_load_all(resp.data["yaml"])
            if doc is not None
        ]
        trigger = next(
            doc for doc in exported_docs
            if doc["kind"] == "trigger"
            and doc["metadata"]["name"] == "Plant Numbered Seed"
        )
        expected_ref = f"itemdefinition.{numeric_slug}"
        self.assertEqual(
            trigger["spec"]["conditions"]["item_present"]["item"],
            expected_ref,
        )
        self.assertEqual(
            trigger["spec"]["steps"][0]["actions"][0]["item"],
            expected_ref,
        )

    def test_world_export_preserves_typed_numeric_mobdefinition_slugs(self):
        legacy_id_definition = MobDefinition.objects.create(
            world=self.world,
            slug="legacy-export-id-definition",
            name="a legacy export ID definition",
        )
        numeric_slug = str(legacy_id_definition.id)
        MobDefinition.objects.create(
            world=self.world,
            slug=numeric_slug,
            name="a numbered export commander",
        )
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.start_room.id,
            name="Free Numbered Commander",
            match="free numbered commander",
            script="",
            conditions=json.dumps({
                "mob_present": {
                    "ref": f"mobdefinition.{numeric_slug}",
                    "where": {
                        "eq": [
                            "actor.definition_id",
                            f"mobdefinition.{numeric_slug}",
                        ],
                    },
                },
            }),
            steps=[
                {
                    "after_seconds": 0,
                    "actions": [
                        {
                            "type": "set_mob",
                            "room": "trigger_room",
                            "mob": f"mobdefinition.{numeric_slug}",
                            "fields": {"attackable": True},
                        },
                    ],
                },
            ],
            display_action_in_room=True,
            is_active=True,
        )

        resp = self.client.get(self.export_ep)

        self.assertEqual(resp.status_code, 200, resp.data)
        exported_docs = [
            doc for doc in yaml.safe_load_all(resp.data["yaml"])
            if doc is not None
        ]
        trigger = next(
            doc for doc in exported_docs
            if doc["kind"] == "trigger"
            and doc["metadata"]["name"] == "Free Numbered Commander"
        )
        expected_ref = f"mobdefinition.{numeric_slug}"
        self.assertEqual(
            trigger["spec"]["conditions"]["mob_present"],
            {
                "ref": expected_ref,
                "where": {
                    "eq": [
                        "actor.definition_id",
                        expected_ref,
                    ],
                },
            },
        )
        self.assertEqual(
            trigger["spec"]["steps"][0]["actions"][0]["mob"],
            expected_ref,
        )

    def test_trigger_export_uses_preloaded_refs_without_per_action_queries(self):
        trigger = (
            Trigger.objects.select_related("target_type")
            .get(world=self.world, name="Plant Barley")
        )
        # Resolve the generic target before query counting; this test isolates
        # condition/action reference canonicalization from the older target path.
        self.assertEqual(trigger.target, self.start_room)
        entity_ref_cache = builder_world_export._build_entity_ref_cache(
            item_definitions=[
                self.barley_seed,
                self.barley_seedling,
                self.barley_growing,
            ],
            mob_definitions=[self.quartermaster],
        )

        with self.assertNumQueries(0):
            manifest = builder_world_export._serialize_trigger_manifest(
                trigger,
                world=self.world,
                entity_ref_cache=entity_ref_cache,
            )

        self.assertEqual(
            manifest["spec"]["conditions"]["item_present"]["item"],
            "itemdefinition.barley-seed",
        )
        self.assertEqual(
            manifest["spec"]["steps"][1]["actions"][0]["with"],
            "itemdefinition.barley-growing",
        )
        self.assertEqual(
            manifest["spec"]["steps"][0]["actions"][2]["currency"],
            "obol",
        )
        self.assertEqual(
            manifest["spec"]["steps"][0]["actions"][3]["subject"]["mob"],
            "mobdefinition.quartermaster",
        )
        self.assertEqual(
            manifest["spec"]["steps"][0]["actions"][3]["subject"]["where"],
            {
                "eq": [
                    "state.character.on_duty",
                    True,
                ],
            },
        )

    def test_zone_detail_returns_apply_and_delete_yaml(self):
        detail_ep = reverse(
            "builder-zone-detail",
            args=[self.world.pk, self.harbor_zone.pk],
        )

        resp = self.client.get(detail_ep)

        self.assertEqual(resp.status_code, 200, resp.data)
        manifest = yaml.safe_load(resp.data["yaml"])
        self.assertEqual(manifest["kind"], "zone")
        self.assertEqual(manifest["metadata"]["ref"], f"zone@{self.harbor_zone.relative_id}")
        self.assertEqual(manifest["metadata"]["name"], "Harbor District")
        self.assertNotIn("is_warzone", manifest["spec"])

        delete_manifest = yaml.safe_load(resp.data["delete_yaml"])
        self.assertEqual(delete_manifest["kind"], "zone")
        self.assertEqual(delete_manifest["operation"], "delete")
        self.assertEqual(delete_manifest["metadata"]["ref"], f"zone@{self.harbor_zone.relative_id}")

    def test_room_manifest_endpoint_returns_yaml_without_expanding_room_reads(self):
        manifest_ep = reverse(
            "builder-room-manifest",
            args=[self.world.pk, self.start_room.pk],
        )

        manifest_resp = self.client.get(manifest_ep)

        self.assertEqual(manifest_resp.status_code, 200, manifest_resp.data)
        manifest = yaml.safe_load(manifest_resp.data["yaml"])
        self.assertEqual(manifest_resp.data["manifest"], manifest)
        self.assertEqual(manifest["kind"], "room")
        self.assertEqual(
            manifest["metadata"]["ref"],
            f"room@{self.start_room.x},{self.start_room.y},{self.start_room.z}",
        )
        self.assertEqual(manifest["metadata"]["name"], "Old Gate")
        self.assertEqual(manifest["spec"]["zone"], f"zone@{self.start_zone.relative_id}")
        self.assertEqual(
            manifest["spec"]["exits"]["east"],
            f"room@{self.harbor_room.x},{self.harbor_room.y},{self.harbor_room.z}",
        )

        detail_resp = self.client.get(
            reverse(
                "builder-room-detail",
                args=[self.world.pk, self.start_room.pk],
            )
        )
        self.assertEqual(detail_resp.status_code, 200, detail_resp.data)
        self.assertNotIn("manifest", detail_resp.data)
        self.assertNotIn("yaml", detail_resp.data)

        list_resp = self.client.get(
            reverse("builder-room-list", args=[self.world.pk])
        )
        self.assertEqual(list_resp.status_code, 200, list_resp.data)
        self.assertTrue(list_resp.data["results"])
        for room_payload in list_resp.data["results"]:
            self.assertNotIn("manifest", room_payload)
            self.assertNotIn("yaml", room_payload)

    def test_rank_two_builder_can_read_room_manifest_without_assignment(self):
        builder_user = self.create_user("room-manifest-builder@example.com")
        builder = WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        self.client.force_authenticate(builder_user)

        resp = self.client.get(
            reverse(
                "builder-room-manifest",
                args=[self.world.pk, self.start_room.pk],
            )
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["manifest"]["kind"], "room")

        apply_resp = self.client.post(
            self.apply_ep,
            {"manifest": resp.data["yaml"]},
            format="json",
        )
        self.assertEqual(apply_resp.status_code, 403, apply_resp.data)

        BuilderAssignment.objects.create(
            builder=builder,
            assignment=self.start_room,
        )
        apply_resp = self.client.post(
            self.apply_ep,
            {"manifest": resp.data["yaml"]},
            format="json",
        )
        self.assertEqual(apply_resp.status_code, 200, apply_resp.data)
        self.assertEqual(apply_resp.data["operation"], "updated")

    def test_room_manifest_apply_rejects_non_mapping_metadata(self):
        resp = self.client.post(
            self.apply_ep,
            {
                "manifest": """kind: room
metadata: invalid
spec: {}
""",
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("metadata must be a mapping", str(resp.data))

    def test_copied_zone_yaml_updates_existing_zone_by_ref(self):
        detail_ep = reverse(
            "builder-zone-detail",
            args=[self.world.pk, self.harbor_zone.pk],
        )
        detail_resp = self.client.get(detail_ep)
        self.assertEqual(detail_resp.status_code, 200, detail_resp.data)
        manifest = yaml.safe_load(detail_resp.data["yaml"])
        manifest["metadata"]["name"] = "Renamed Harbor"

        apply_resp = self.client.post(
            self.apply_ep,
            {"manifest": yaml.safe_dump(manifest, sort_keys=False)},
            format="json",
        )

        self.assertEqual(apply_resp.status_code, 200, apply_resp.data)
        self.assertEqual(apply_resp.data["kind"], "zone")
        self.assertEqual(apply_resp.data["operation"], "updated")
        self.harbor_zone.refresh_from_db()
        self.assertEqual(self.harbor_zone.name, "Renamed Harbor")

    def test_zone_delete_manifest_removes_empty_zone(self):
        empty_zone = Zone.objects.create(
            world=self.world,
            name="Empty District",
        )
        detail_ep = reverse(
            "builder-zone-detail",
            args=[self.world.pk, empty_zone.pk],
        )
        detail_resp = self.client.get(detail_ep)
        self.assertEqual(detail_resp.status_code, 200, detail_resp.data)

        apply_resp = self.client.post(
            self.apply_ep,
            {"manifest": detail_resp.data["delete_yaml"]},
            format="json",
        )

        self.assertEqual(apply_resp.status_code, 200, apply_resp.data)
        self.assertEqual(apply_resp.data["kind"], "zone")
        self.assertEqual(apply_resp.data["operation"], "deleted")
        self.assertEqual(apply_resp.data["zone"]["manifest_ref"], f"zone@{empty_zone.relative_id}")
        self.assertFalse(Zone.objects.filter(pk=empty_zone.pk).exists())
