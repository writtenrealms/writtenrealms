import json

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext

import yaml

from rest_framework import serializers
from rest_framework.reverse import reverse

from builders import world_export as builder_world_export
from builders.currencies import create_currency, replace_starting_balances
from builders.models import (
    AbilityDefinition,
    BuilderAssignment,
    Currency,
    ItemDefinition,
    LastViewedRoom,
    MobDefinition,
    Path,
    PathRoom,
    SpawnEntry,
    SpawnPlan,
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
from spawns.models import Player
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

    def _create_lobby_import_target(self, *, name):
        response = self.client.post(
            reverse("builder-world-list"),
            {
                "name": name,
                "is_multiplayer": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)

        target_world = self.world.__class__.objects.get(pk=response.data["id"])
        scaffold = target_world.rooms.get(relative_id=1)
        builder_player = Player.objects.get(
            world__context=target_world,
            is_builder=True,
        )
        bookmark = LastViewedRoom.objects.get(
            world=target_world,
            user=self.user,
        )
        self.assertEqual(builder_player.room_id, scaffold.id)
        self.assertEqual(bookmark.room_id, scaffold.id)
        return target_world, scaffold, builder_player, bookmark

    def _sparse_complete_world_manifest(self):
        return yaml.safe_dump_all(
            [
                {
                    "apiVersion": "writtenrealms.com/v1alpha3",
                    "kind": "zone",
                    "metadata": {
                        "ref": "zone@6",
                        "name": "Imported Zone",
                    },
                    "spec": {},
                },
                {
                    "apiVersion": "writtenrealms.com/v1alpha3",
                    "kind": "room",
                    "metadata": {
                        "ref": "room@193",
                        "name": "Imported Origin",
                    },
                    "spec": {
                        "coordinates": {"x": 0, "y": 0, "z": 0},
                        "zone": "zone@6",
                    },
                },
                {
                    "apiVersion": "writtenrealms.com/v1alpha3",
                    "kind": "room",
                    "metadata": {
                        "ref": "room@492",
                        "name": "Imported Starting Room",
                    },
                    "spec": {
                        "coordinates": {"x": 4, "y": 9, "z": 0},
                        "zone": "zone@6",
                    },
                },
                {
                    "apiVersion": "writtenrealms.com/v1alpha3",
                    "kind": "world",
                    "spec": {
                        "starting_room": "room@492",
                        "death_room": "room@193",
                    },
                },
            ],
            sort_keys=False,
        )

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
            respawn_mode="fixed",
            respawn_seconds=120,
            door_reset_mode="fixed",
            door_reset_seconds=120,
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
        self.harbor_bash = AbilityDefinition.objects.create(
            world=self.world,
            slug="harbor-bash",
            name="Harbor Bash",
            command_verbs=["harbor_bash"],
            components=[
                {
                    "type": "effect",
                    "effect": "stun",
                    "apply": "on_hit",
                    "target": "ability.target",
                    "category": "debuff",
                    "duration": {"rounds": 1},
                    "text": {"label": "Harbor Bash"},
                },
            ],
        )
        self.quartermaster.combat_abilities = [
            {"ability": self.harbor_bash.slug, "weight": 1},
        ]
        self.quartermaster.save(update_fields=["combat_abilities"])

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
                            "type": "grant_currency",
                            "actor": "trigger_actor",
                            "currency": "obol",
                            "amount": 3,
                        },
                        {
                            "type": "command",
                            "subject": {
                                "type": "mob",
                                "room": f"room.{self.harbor_room.id}",
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
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ZONE,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Zone),
            target_id=self.harbor_zone.id,
            name="Survey Harbor Zone",
            match="survey harbor zone",
            script="/cmd room -- /echo -- The harbor is busy.",
            display_action_in_room=True,
            is_active=True,
        )
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(self.world.__class__),
            target_id=self.world.id,
            name="Survey Export World",
            match="survey export world",
            script="/cmd room -- /echo -- The whole world listens.",
            display_action_in_room=True,
            is_active=True,
        )
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_WORLD,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(ItemDefinition),
            target_id=self.brass_key.id,
            name="Inspect Brass Key",
            match="inspect brass key",
            script="/cmd room -- /echo -- The key is brightly polished.",
            display_action_in_room=True,
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
            + ["craftmaterial"] * resp.data["summary"]["craft_materials"]
            + ["itemdefinition"] * resp.data["summary"]["item_definitions"]
            + ["itembundle"] * resp.data["summary"]["item_bundles"]
            + ["merchantprofile"] * resp.data["summary"]["merchant_profiles"]
            + ["craftingrecipe"] * resp.data["summary"]["crafting_recipes"]
            + ["craftingprofile"] * resp.data["summary"]["crafting_profiles"]
            + ["ability"] * resp.data["summary"]["abilities"]
            + ["trainerprofile"] * resp.data["summary"]["trainer_profiles"]
            + ["faction"] * resp.data["summary"]["factions"]
            + ["zone"] * resp.data["summary"]["zones"]
            + ["room"] * resp.data["summary"]["rooms"]
            + ["path"] * resp.data["summary"]["paths"]
            + ["mobdefinition"] * resp.data["summary"]["mob_definitions"]
            + ["spawnplan"] * resp.data["summary"]["spawn_plans"]
            + ["social"] * resp.data["summary"]["socials"]
            + ["questarc"] * resp.data["summary"]["quest_arcs"]
            + ["quest"] * resp.data["summary"]["quests"]
            + ["trigger"] * resp.data["summary"]["triggers"]
            + ["world"]
        )
        self.assertEqual([doc["kind"] for doc in exported_docs], expected_kinds)
        ability_doc = next(
            doc
            for doc in exported_docs
            if doc["kind"] == "ability"
            and doc["metadata"]["slug"] == self.harbor_bash.slug
        )
        mob_doc = next(
            doc
            for doc in exported_docs
            if doc["kind"] == "mobdefinition"
            and doc["metadata"]["slug"] == self.quartermaster.slug
        )
        self.assertLess(
            exported_docs.index(ability_doc),
            exported_docs.index(mob_doc),
        )
        self.assertEqual(
            ability_doc["spec"]["components"][0]["scope"],
            "encounter",
        )

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
            if doc["kind"] == "room"
            and doc["metadata"]["ref"] == f"room@{self.harbor_room.relative_id}"
        )
        self.assertEqual(room_doc["spec"]["zone"], f"zone@{self.harbor_zone.relative_id}")
        self.assertEqual(
            room_doc["spec"]["coordinates"],
            {
                "x": self.harbor_room.x,
                "y": self.harbor_room.y,
                "z": self.harbor_room.z,
            },
        )
        world_doc = exported_docs[-1]
        self.assertEqual(world_doc["spec"]["initial_state"], {"weather": "windy"})
        start_room_doc = next(
            doc
            for doc in exported_docs
            if doc["kind"] == "room"
            and doc["metadata"]["ref"]
            == f"room@{self.start_room.relative_id}"
        )
        self.assertEqual(
            start_room_doc["spec"]["initial_state"],
            {"gate_open": False},
        )

        path_doc = next(doc for doc in exported_docs if doc["kind"] == "path")
        self.assertEqual(path_doc["metadata"]["ref"], f"path@{self.patrol_path.relative_id}")
        self.assertEqual(path_doc["metadata"]["name"], "Patrol Loop")
        self.assertEqual(path_doc["spec"]["zone"], f"zone@{self.harbor_zone.relative_id}")
        self.assertEqual(path_doc["spec"]["entry_room"], f"room@{self.harbor_room.relative_id}")
        self.assertEqual(
            path_doc["spec"]["rooms"],
            [
                f"room@{self.start_room.relative_id}",
                f"room@{self.harbor_room.relative_id}",
            ],
        )
        trigger_targets = {
            document["metadata"]["name"]: document["spec"]["target"]
            for document in exported_docs
            if document["kind"] == "trigger"
        }
        self.assertEqual(
            trigger_targets,
            {
                "Open Harbor Gate": f"room@{self.start_room.relative_id}",
                "Plant Barley": f"room@{self.start_room.relative_id}",
                "Quartermaster Greeting": (
                    f"mobdefinition.{self.quartermaster.slug}"
                ),
                "Survey Harbor Zone": f"zone@{self.harbor_zone.relative_id}",
                "Survey Export World": "world",
                "Inspect Brass Key": f"itemdefinition.{self.brass_key.slug}",
            },
        )
        self.assertTrue(
            all(
                document["apiVersion"] == "writtenrealms.com/v1alpha3"
                for document in exported_docs
            )
        )

    def test_world_export_round_trips_into_fresh_world(self):
        self.maxDiff = None
        self.harbor_zone.default_roam_chance = 23
        self.harbor_zone.save(update_fields=["default_roam_chance"])
        source_patrol_plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.start_zone,
            slug="harbor-patrols",
            name="Harbor Patrols",
            default_roam_chance=47,
            respawn_policy={"mode": "fixed", "seconds": 0},
        )
        SpawnEntry.objects.create(
            plan=source_patrol_plan,
            slug="quartermaster-patrol",
            source=f"mobdefinition.{self.quartermaster.slug}",
            target_path=self.patrol_path,
            count=1,
        )
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
        imported_harbor = target_world.rooms.get(
            relative_id=self.harbor_room.relative_id,
        )
        self.assertNotEqual(imported_harbor.id, self.harbor_room.id)
        imported_open_gate = target_world.triggers.get(name="Open Harbor Gate")
        self.assertNotEqual(imported_open_gate.target_id, self.start_room.id)
        self.assertEqual(
            imported_open_gate.target.relative_id,
            self.start_room.relative_id,
        )
        imported_item_trigger = target_world.triggers.get(
            name="Inspect Brass Key"
        )
        self.assertNotEqual(imported_item_trigger.target_id, self.brass_key.id)
        self.assertEqual(imported_item_trigger.target.slug, self.brass_key.slug)
        imported_plant_trigger = target_world.triggers.get(name="Plant Barley")
        self.assertEqual(
            imported_plant_trigger.steps[0]["actions"][4]["subject"]["room"],
            f"room@{self.harbor_room.relative_id}",
        )
        imported_harbor_zone = target_world.zones.get(
            relative_id=self.harbor_zone.relative_id,
        )
        imported_patrol_plan = target_world.spawn_plans.get(
            slug=source_patrol_plan.slug,
        )
        self.assertEqual(imported_harbor_zone.default_roam_chance, 23)
        self.assertEqual(imported_patrol_plan.default_roam_chance, 47)

    def test_complete_import_replaces_lobby_scaffold_and_rehomes_builder(self):
        (
            target_world,
            scaffold,
            builder_player,
            bookmark,
        ) = self._create_lobby_import_target(name="Sparse Import Target")
        manifest = self._sparse_complete_world_manifest()
        apply_ep = reverse(
            "builder-world-manifest-apply",
            args=[target_world.pk],
        )

        response = self.client.post(
            apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(target_world.rooms.filter(pk=scaffold.pk).exists())
        self.assertEqual(
            set(target_world.rooms.values_list("relative_id", flat=True)),
            {193, 492},
        )
        imported_origin = target_world.rooms.get(relative_id=193)
        imported_start = target_world.rooms.get(relative_id=492)
        self.assertEqual(
            (imported_origin.x, imported_origin.y, imported_origin.z),
            (0, 0, 0),
        )

        target_world.config.refresh_from_db()
        builder_player.refresh_from_db()
        bookmark.refresh_from_db()
        self.assertEqual(target_world.config.starting_room_id, imported_start.id)
        self.assertEqual(target_world.config.death_room_id, imported_origin.id)
        self.assertEqual(builder_player.room_id, imported_start.id)
        self.assertEqual(bookmark.room_id, imported_start.id)

        repeat_response = self.client.post(
            apply_ep,
            {"manifest": manifest},
            format="json",
        )
        self.assertEqual(repeat_response.status_code, 200, repeat_response.data)
        builder_player.refresh_from_db()
        bookmark.refresh_from_db()
        self.assertEqual(builder_player.room_id, imported_start.id)
        self.assertEqual(bookmark.room_id, imported_start.id)

    def test_complete_import_keeps_scaffold_with_non_builder_player(self):
        (
            target_world,
            scaffold,
            _builder_player,
            _bookmark,
        ) = self._create_lobby_import_target(name="Occupied Import Target")
        visitor = Player.objects.create(
            world=target_world.spawned_worlds.get(),
            user=self.create_user("sparse-import-visitor@example.com"),
            name="Visitor",
            room=scaffold,
        )

        response = self.client.post(
            reverse(
                "builder-world-manifest-apply",
                args=[target_world.pk],
            ),
            {"manifest": self._sparse_complete_world_manifest()},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("room@1", str(response.data))
        self.assertEqual(
            list(target_world.rooms.values_list("relative_id", flat=True)),
            [1],
        )
        visitor.refresh_from_db()
        self.assertEqual(visitor.room_id, scaffold.id)

    def test_batch_preflights_room_create_permission_before_zone_center_reservation(self):
        builder_user = self.create_user("assigned-zone-builder@example.com")
        builder = WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        BuilderAssignment.objects.create(
            builder=builder,
            assignment=self.harbor_zone,
        )
        self.client.force_authenticate(builder_user)

        original_center_id = self.harbor_zone.center_id
        new_relative_id = (
            max(self.world.rooms.values_list("relative_id", flat=True)) + 100
        )
        manifest = yaml.safe_dump_all(
            [
                {
                    "apiVersion": "writtenrealms.com/v1alpha2",
                    "kind": "zone",
                    "metadata": {
                        "ref": f"zone@{self.harbor_zone.relative_id}",
                        "name": self.harbor_zone.name,
                    },
                    "spec": {
                        "center": f"room@{new_relative_id}",
                    },
                },
                {
                    "apiVersion": "writtenrealms.com/v1alpha2",
                    "kind": "room",
                    "metadata": {
                        "ref": f"room@{new_relative_id}",
                        "name": "Unauthorized Reserved Room",
                    },
                    "spec": {
                        "coordinates": {
                            "x": 500,
                            "y": 500,
                            "z": 500,
                        },
                    },
                },
            ],
            sort_keys=False,
        )

        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("create rooms", str(response.data).lower())
        self.assertFalse(
            self.world.rooms.filter(relative_id=new_relative_id).exists()
        )
        self.harbor_zone.refresh_from_db()
        self.assertEqual(self.harbor_zone.center_id, original_center_id)

    def test_batch_zone_center_accepts_newly_reserved_room_before_room_apply(self):
        new_zone_relative_id = (
            max(self.world.zones.values_list("relative_id", flat=True)) + 100
        )
        new_room_relative_id = (
            max(self.world.rooms.values_list("relative_id", flat=True)) + 100
        )
        new_x = max(self.world.rooms.values_list("x", flat=True)) + 100
        manifest = yaml.safe_dump_all(
            [
                {
                    "apiVersion": "writtenrealms.com/v1alpha3",
                    "kind": "zone",
                    "metadata": {
                        "ref": f"zone@{new_zone_relative_id}",
                        "name": "Forward Center Zone",
                    },
                    "spec": {
                        "center": f"room@{new_room_relative_id}",
                    },
                },
                {
                    "apiVersion": "writtenrealms.com/v1alpha3",
                    "kind": "room",
                    "metadata": {
                        "ref": f"room@{new_room_relative_id}",
                        "name": "Forward Center Room",
                    },
                    "spec": {
                        "coordinates": {"x": new_x, "y": 0, "z": 0},
                        "zone": f"zone@{new_zone_relative_id}",
                    },
                },
            ],
            sort_keys=False,
        )

        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        imported_zone = self.world.zones.get(
            relative_id=new_zone_relative_id,
        )
        imported_room = self.world.rooms.get(
            relative_id=new_room_relative_id,
        )
        self.assertEqual(imported_zone.center_id, imported_room.id)
        self.assertEqual(imported_room.zone_id, imported_zone.id)

    def test_batch_rolls_back_deferred_center_assigned_to_another_zone(self):
        new_room_relative_id = (
            max(self.world.rooms.values_list("relative_id", flat=True)) + 100
        )
        new_x = max(self.world.rooms.values_list("x", flat=True)) + 100
        original_center_id = self.harbor_zone.center_id
        manifest = yaml.safe_dump_all(
            [
                {
                    "apiVersion": "writtenrealms.com/v1alpha3",
                    "kind": "zone",
                    "metadata": {
                        "ref": f"zone@{self.harbor_zone.relative_id}",
                        "name": self.harbor_zone.name,
                    },
                    "spec": {
                        "center": f"room@{new_room_relative_id}",
                    },
                },
                {
                    "apiVersion": "writtenrealms.com/v1alpha3",
                    "kind": "room",
                    "metadata": {
                        "ref": f"room@{new_room_relative_id}",
                        "name": "Mismatched Deferred Center",
                    },
                    "spec": {
                        "coordinates": {"x": new_x, "y": 0, "z": 0},
                        "zone": f"zone@{self.start_zone.relative_id}",
                    },
                },
            ],
            sort_keys=False,
        )

        response = self.client.post(
            self.apply_ep,
            {"manifest": manifest},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("is not assigned to that zone", str(response.data))
        self.assertFalse(
            self.world.rooms.filter(relative_id=new_room_relative_id).exists()
        )
        self.harbor_zone.refresh_from_db()
        self.assertEqual(self.harbor_zone.center_id, original_center_id)

    def test_import_stages_moved_placeholder_before_reusing_origin(self):
        self.start_room.x = 20
        self.start_room.save(update_fields=["x"])
        origin_room = Room.objects.create(
            world=self.world,
            zone=self.start_zone,
            name="Reoccupied Origin",
            x=0,
            y=0,
            z=0,
        )

        source_response = self.client.get(self.export_ep)
        self.assertEqual(source_response.status_code, 200, source_response.data)
        source_documents = [
            document
            for document in yaml.safe_load_all(source_response.data["yaml"])
            if document is not None
        ]

        target_world = self.world.__class__.objects.new_world(
            name="Moved Placeholder Import",
            author=self.user,
            config=WorldConfig.objects.create(),
        )
        response = self.client.post(
            reverse(
                "builder-world-manifest-apply",
                args=[target_world.pk],
            ),
            {"manifest": source_response.data["yaml"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        imported_start = target_world.rooms.get(
            relative_id=self.start_room.relative_id,
        )
        imported_origin = target_world.rooms.get(
            relative_id=origin_room.relative_id,
        )
        self.assertEqual(
            (imported_start.x, imported_start.y, imported_start.z),
            (20, 0, 0),
        )
        self.assertEqual(
            (imported_origin.x, imported_origin.y, imported_origin.z),
            (0, 0, 0),
        )

        reexport = self.client.get(
            reverse("builder-world-export", args=[target_world.pk]),
        )
        self.assertEqual(reexport.status_code, 200, reexport.data)
        self.assertEqual(
            [
                document
                for document in yaml.safe_load_all(reexport.data["yaml"])
                if document is not None
            ],
            source_documents,
        )

    def test_room_move_updates_coordinates_without_changing_manifest_references(self):
        spawn_plan = SpawnPlan.objects.create(
            world=self.world,
            zone=self.harbor_zone,
            slug="charon-ferry",
            name="Charon Ferry",
            respawn_policy={"mode": "inherit_zone"},
        )
        SpawnEntry.objects.create(
            plan=spawn_plan,
            slug="charon-entrance",
            source=f"mobdefinition.{self.quartermaster.slug}",
            target_room=self.harbor_room,
            count=1,
        )
        Trigger.objects.create(
            world=self.world,
            scope=adv_consts.TRIGGER_SCOPE_ROOM,
            kind=adv_consts.TRIGGER_KIND_COMMAND,
            target_type=ContentType.objects.get_for_model(Room),
            target_id=self.harbor_room.id,
            name="Moved Harbor Trigger",
            match="inspect moved harbor",
            script="/cmd room -- /echo -- Still here.",
            display_action_in_room=True,
        )
        stable_ref = f"room@{self.harbor_room.relative_id}"

        self.harbor_room.x = 12
        self.harbor_room.y = 3
        self.harbor_room.save(update_fields=["x", "y"])

        response = self.client.get(self.export_ep)
        self.assertEqual(response.status_code, 200, response.data)
        documents = [
            document
            for document in yaml.safe_load_all(response.data["yaml"])
            if document is not None
        ]
        room_manifest = next(
            document
            for document in documents
            if document["kind"] == "room"
            and document["metadata"]["ref"] == stable_ref
        )
        spawn_manifest = next(
            document
            for document in documents
            if document["kind"] == "spawnplan"
            and document["metadata"]["slug"] == "charon-ferry"
        )
        trigger_manifest = next(
            document
            for document in documents
            if document["kind"] == "trigger"
            and document["metadata"]["name"] == "Moved Harbor Trigger"
        )

        self.assertEqual(
            room_manifest["spec"]["coordinates"],
            {"x": 12, "y": 3, "z": 0},
        )
        self.assertEqual(
            spawn_manifest["spec"]["entries"][0]["target"],
            stable_ref,
        )
        self.assertEqual(trigger_manifest["spec"]["target"], stable_ref)
        self.assertEqual(
            documents[-1]["spec"]["starting_room"],
            stable_ref,
        )

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

        expected_start_ref = f"room@{self.start_room.relative_id}"
        expected_harbor_ref = f"room@{self.harbor_room.relative_id}"

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
            trigger["spec"]["steps"][0]["actions"][3]["currency"],
            "obol",
        )
        self.assertEqual(
            trigger["spec"]["steps"][0]["actions"][4]["subject"]["mob"],
            "mobdefinition.quartermaster",
        )
        self.assertEqual(
            trigger["spec"]["steps"][0]["actions"][4]["subject"]["room"],
            f"room@{self.harbor_room.relative_id}",
        )
        self.assertEqual(
            trigger["spec"]["steps"][0]["actions"][4]["subject"]["where"],
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
        room_ref_cache = builder_world_export._build_room_ref_cache(self.world)

        with self.assertNumQueries(0):
            manifest = builder_world_export._serialize_trigger_manifest(
                trigger,
                world=self.world,
                entity_ref_cache=entity_ref_cache,
                room_ref_cache=room_ref_cache,
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
            manifest["spec"]["steps"][0]["actions"][3]["currency"],
            "obol",
        )
        self.assertEqual(
            manifest["spec"]["steps"][0]["actions"][4]["subject"]["mob"],
            "mobdefinition.quartermaster",
        )
        self.assertEqual(
            manifest["spec"]["steps"][0]["actions"][4]["subject"]["room"],
            f"room@{self.harbor_room.relative_id}",
        )
        self.assertEqual(
            manifest["spec"]["steps"][0]["actions"][4]["subject"]["where"],
            {
                "eq": [
                    "state.character.on_duty",
                    True,
                ],
            },
        )

    def test_world_export_room_queries_do_not_scale_per_room(self):
        # Warm process-level caches before comparing the two export shapes.
        builder_world_export.serialize_world_documents(self.world)
        with CaptureQueriesContext(connection) as baseline_queries:
            builder_world_export.serialize_world_documents(self.world)

        for index in range(8):
            room = Room.objects.create(
                world=self.world,
                zone=self.zone,
                name=f"Query Regression Room {index}",
                x=100 + index,
                y=100,
                z=0,
            )
            RoomFlag.objects.create(
                room=room,
                code=adv_consts.ROOM_FLAG_NO_ROAM,
            )
            RoomDetail.objects.create(
                room=room,
                keywords=f"detail-{index}",
                description="A query-regression detail.",
            )

        with CaptureQueriesContext(connection) as expanded_queries:
            builder_world_export.serialize_world_documents(self.world)

        self.assertLessEqual(
            len(expanded_queries),
            len(baseline_queries) + 1,
            "Room export query count grew with the number of rooms.",
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
            f"room@{self.start_room.relative_id}",
        )
        self.assertEqual(
            manifest["spec"]["coordinates"],
            {
                "x": self.start_room.x,
                "y": self.start_room.y,
                "z": self.start_room.z,
            },
        )
        self.assertEqual(manifest["metadata"]["name"], "Old Gate")
        self.assertEqual(manifest["spec"]["zone"], f"zone@{self.start_zone.relative_id}")
        self.assertEqual(
            manifest["spec"]["exits"]["east"],
            f"room@{self.harbor_room.relative_id}",
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
        self.assertEqual(
            apply_resp.data["room"]["relative_id"],
            self.start_room.relative_id,
        )
        self.assertEqual(
            apply_resp.data["room"]["manifest_ref"],
            f"room@{self.start_room.relative_id}",
        )
        self.assertEqual(
            apply_resp.data["room"]["ref"],
            apply_resp.data["room"]["manifest_ref"],
        )

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
            {
                "manifest": yaml.safe_dump(manifest, sort_keys=False),
                "expected_kind": "zone",
                "expected_ref": f"zone@{self.harbor_zone.relative_id}",
                "expected_operation": "apply",
                "expected_result": "updated",
            },
            format="json",
        )

        self.assertEqual(apply_resp.status_code, 200, apply_resp.data)
        self.assertEqual(apply_resp.data["kind"], "zone")
        self.assertEqual(apply_resp.data["operation"], "updated")
        self.harbor_zone.refresh_from_db()
        self.assertEqual(self.harbor_zone.name, "Renamed Harbor")

    def test_zone_center_apply_locks_room_before_zone(self):
        manifest = {
            "apiVersion": "writtenrealms.com/v1alpha3",
            "kind": "zone",
            "metadata": {
                "ref": f"zone@{self.harbor_zone.relative_id}",
                "name": self.harbor_zone.name,
            },
            "spec": {
                "center": f"room@{self.harbor_room.relative_id}",
            },
        }

        with CaptureQueriesContext(connection) as queries:
            builder_world_export.apply_zone_manifest(
                world=self.world,
                manifest=manifest,
                require_existing=True,
            )

        lock_target_order = []
        for query in queries.captured_queries:
            sql = query["sql"].lower()
            if 'from "worlds_room"' in sql:
                lock_target_order.append("room")
            elif 'from "worlds_zone"' in sql:
                lock_target_order.append("zone")
        first_zone_index = lock_target_order.index("zone")
        self.assertGreaterEqual(first_zone_index, 2)
        self.assertEqual(
            lock_target_order[:first_zone_index],
            ["room"] * first_zone_index,
        )

    def test_zone_apply_without_center_does_not_query_rooms(self):
        manifest = {
            "apiVersion": "writtenrealms.com/v1alpha3",
            "kind": "zone",
            "metadata": {
                "ref": f"zone@{self.harbor_zone.relative_id}",
                "name": self.harbor_zone.name,
            },
            "spec": {
                "notes": self.harbor_zone.notes,
            },
        }

        with CaptureQueriesContext(connection) as queries:
            builder_world_export.apply_zone_manifest(
                world=self.world,
                manifest=manifest,
                require_existing=True,
            )

        selected_tables = [
            "room" if 'from "worlds_room"' in query["sql"].lower()
            else "zone" if 'from "worlds_zone"' in query["sql"].lower()
            else None
            for query in queries.captured_queries
        ]
        self.assertIn("zone", selected_tables)
        self.assertNotIn("room", selected_tables)

    def test_assigned_zone_builder_cannot_reassign_room_with_center_update(self):
        builder_user = self.create_user("zone-center-assignee@example.com")
        builder = WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=2,
        )
        BuilderAssignment.objects.create(
            builder=builder,
            assignment=self.harbor_zone,
        )
        self.client.force_authenticate(builder_user)
        original_center_id = self.harbor_zone.center_id
        original_name = self.harbor_zone.name
        manifest = {
            "apiVersion": "writtenrealms.com/v1alpha3",
            "kind": "zone",
            "metadata": {
                "ref": f"zone@{self.harbor_zone.relative_id}",
                "name": "Illicit Assigned-Zone Update",
            },
            "spec": {
                "center": f"room@{self.start_room.relative_id}",
            },
        }

        response = self.client.post(
            self.apply_ep,
            {
                "manifest": yaml.safe_dump(manifest, sort_keys=False),
                "expected_result": "updated",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("already assigned to this zone", str(response.data))
        self.start_room.refresh_from_db()
        self.harbor_zone.refresh_from_db()
        self.assertEqual(self.start_room.zone_id, self.start_zone.id)
        self.assertEqual(self.harbor_zone.center_id, original_center_id)
        self.assertEqual(self.harbor_zone.name, original_name)

    def test_rank_three_builder_cannot_reassign_room_with_center_apply(self):
        builder_user = self.create_user("zone-center-rank-three@example.com")
        WorldBuilder.objects.create(
            world=self.world,
            user=builder_user,
            builder_rank=3,
        )
        self.client.force_authenticate(builder_user)
        original_center_id = self.harbor_zone.center_id
        original_name = self.harbor_zone.name
        manifest = {
            "apiVersion": "writtenrealms.com/v1alpha3",
            "kind": "zone",
            "metadata": {
                "ref": f"zone@{self.harbor_zone.relative_id}",
                "name": "Illicit Rank-Three Update",
            },
            "spec": {
                "center": f"room@{self.start_room.relative_id}",
            },
        }

        response = self.client.post(
            self.apply_ep,
            {"manifest": yaml.safe_dump(manifest, sort_keys=False)},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("already assigned to this zone", str(response.data))
        self.start_room.refresh_from_db()
        self.harbor_zone.refresh_from_db()
        self.assertEqual(self.start_room.zone_id, self.start_zone.id)
        self.assertEqual(self.harbor_zone.center_id, original_center_id)
        self.assertEqual(self.harbor_zone.name, original_name)

    def test_expected_zone_ref_rejects_other_or_unused_ref_before_mutation(self):
        detail_resp = self.client.get(reverse(
            "builder-zone-detail",
            args=[self.world.pk, self.harbor_zone.pk],
        ))
        self.assertEqual(detail_resp.status_code, 200, detail_resp.data)
        original_start_name = self.start_zone.name
        original_harbor_name = self.harbor_zone.name
        unused_relative_id = max(
            Zone.objects.filter(world=self.world).values_list(
                "relative_id",
                flat=True,
            )
        ) + 100

        for unexpected_ref in (
            f"zone@{self.start_zone.relative_id}",
            f"zone@{unused_relative_id}",
        ):
            with self.subTest(unexpected_ref=unexpected_ref):
                manifest = yaml.safe_load(detail_resp.data["yaml"])
                manifest["metadata"]["ref"] = unexpected_ref
                manifest["metadata"]["name"] = "Wrong Route Zone"

                response = self.client.post(
                    self.apply_ep,
                    {
                        "manifest": yaml.safe_dump(manifest, sort_keys=False),
                        "expected_kind": "zone",
                        "expected_ref": (
                            f"zone@{self.harbor_zone.relative_id}"
                        ),
                        "expected_operation": "apply",
                        "expected_result": "updated",
                    },
                    format="json",
                )

                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("Expected ref", str(response.data))
                self.start_zone.refresh_from_db()
                self.harbor_zone.refresh_from_db()
                self.assertEqual(self.start_zone.name, original_start_name)
                self.assertEqual(self.harbor_zone.name, original_harbor_name)
                self.assertFalse(Zone.objects.filter(
                    world=self.world,
                    relative_id=unused_relative_id,
                ).exists())

    def test_expected_updated_result_rejects_missing_zone_before_create(self):
        detail_resp = self.client.get(reverse(
            "builder-zone-detail",
            args=[self.world.pk, self.harbor_zone.pk],
        ))
        self.assertEqual(detail_resp.status_code, 200, detail_resp.data)
        unused_relative_id = max(
            Zone.objects.filter(world=self.world).values_list(
                "relative_id",
                flat=True,
            )
        ) + 100
        manifest = yaml.safe_load(detail_resp.data["yaml"])
        manifest["metadata"]["ref"] = f"zone@{unused_relative_id}"
        manifest["metadata"]["name"] = "Missing Route Zone"

        response = self.client.post(
            self.apply_ep,
            {
                "manifest": yaml.safe_dump(manifest, sort_keys=False),
                "expected_kind": "zone",
                "expected_ref": f"zone@{unused_relative_id}",
                "expected_operation": "apply",
                "expected_result": "updated",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Zone.objects.filter(
            world=self.world,
            relative_id=unused_relative_id,
        ).exists())

    def test_expected_apply_operation_rejects_zone_delete_before_mutation(self):
        empty_zone = Zone.objects.create(
            world=self.world,
            name="Protected Empty District",
        )
        detail_resp = self.client.get(reverse(
            "builder-zone-detail",
            args=[self.world.pk, empty_zone.pk],
        ))
        self.assertEqual(detail_resp.status_code, 200, detail_resp.data)

        response = self.client.post(
            self.apply_ep,
            {
                "manifest": detail_resp.data["delete_yaml"],
                "expected_kind": "zone",
                "expected_ref": f"zone@{empty_zone.relative_id}",
                "expected_operation": "apply",
                "expected_result": "updated",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Expected operation apply", str(response.data))
        self.assertTrue(Zone.objects.filter(pk=empty_zone.pk).exists())

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
