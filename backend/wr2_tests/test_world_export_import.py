from django.contrib.contenttypes.models import ContentType

import yaml

from rest_framework.reverse import reverse

from builders.models import (
    Currency,
    ItemDefinition,
    MobDefinition,
    Path,
    PathRoom,
    Trigger,
)
from config import constants as adv_consts
from core.scoped_state import STATE_SCOPE_ZONE, replace_state_snapshot
from quests.models import QuestArcTemplate, QuestTemplate
from tests.base import WorldTestCase
from worlds.models import Door, Room, RoomDetail, RoomFlag, WorldConfig, Zone


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
        self.world.name = "Export Source"
        self.world.short_description = "Canonical export test"
        self.world.description = "World export/import should round-trip."
        self.world.motd = "Welcome to the harbor."
        self.world.is_public = True
        self.world.save()

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
        replace_state_snapshot(
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
        self.world.config.starting_gold = 12
        self.world.config.built_by = "WR Export Tests"
        self.world.config.small_background = "https://assets.example/small.png"
        self.world.config.large_background = "https://assets.example/large.png"
        self.world.config.name_exclusions = "admin\nmoderator"
        self.world.config.save()

        self.marks = Currency.objects.create(
            world=self.world,
            code="marks",
            name="Marks",
            is_default=False,
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

        Door.objects.create(
            direction="east",
            from_room=self.start_room,
            to_room=self.harbor_room,
            name="harbor gate",
            key=self.brass_key,
            destroy_key=False,
            default_state=adv_consts.DOOR_STATE_CLOSED,
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
                        "mob_definition": f"mobdefinition.{self.quartermaster.id}",
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
                                                    f"mobdefinition.{self.quartermaster.id}",
                                                ]
                                            },
                                            {
                                                "eq": [
                                                    "event.item.definition_id",
                                                    f"itemdefinition.{self.brass_key.id}",
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
                        "mob_definition": f"mobdefinition.{self.quartermaster.id}",
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
        room_doc = next(
            doc for doc in exported_docs
            if doc["kind"] == "room" and doc["metadata"]["ref"] == f"room@{self.harbor_room.x},{self.harbor_room.y},{self.harbor_room.z}"
        )
        self.assertEqual(room_doc["spec"]["zone"], f"zone@{self.harbor_zone.relative_id}")

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
