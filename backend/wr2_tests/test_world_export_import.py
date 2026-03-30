from django.contrib.contenttypes.models import ContentType

import yaml

from rest_framework.reverse import reverse

from builders.models import (
    Currency,
    ItemTemplate,
    ItemTemplateInventory,
    MobTemplate,
    MobTemplateInventory,
    Trigger,
)
from config import constants as adv_consts
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

        self.brass_key = ItemTemplate.objects.create(
            world=self.world,
            name="a brass key",
            currency=self.marks,
        )
        self.lockbox = ItemTemplate.objects.create(
            world=self.world,
            name="a lockbox",
            type=adv_consts.ITEM_TYPE_CONTAINER,
            capacity=10,
            currency=self.marks,
            notes="Used in the harbor office.",
        )
        ItemTemplateInventory.objects.create(
            container=self.lockbox,
            item_template=self.brass_key,
            probability=100,
            num_copies=1,
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

        self.quartermaster = MobTemplate.objects.create(
            world=self.world,
            name="Quartermaster",
            level=4,
            type=adv_consts.MOB_TYPE_HUMANOID,
            description="Keeps the harbor ledgers.",
            merchant_profit=1.2,
        )
        MobTemplateInventory.objects.create(
            container=self.quartermaster,
            item_template=self.brass_key,
            probability=100,
            num_copies=1,
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
                        "mob_template": f"mobtemplate.{self.quartermaster.id}",
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
                                                    "event.target.template_id",
                                                    f"mobtemplate.{self.quartermaster.id}",
                                                ]
                                            },
                                            {
                                                "eq": [
                                                    "event.item.template_id",
                                                    f"itemtemplate.{self.brass_key.id}",
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
                        "mob_template": f"mobtemplate.{self.quartermaster.id}",
                        "command": "say Delivery received.",
                    }
                ],
                "compromised": [],
                "failed_forward": [],
                "expired": [],
            },
        )

        room_ct = ContentType.objects.get_for_model(Room)
        mob_ct = ContentType.objects.get_for_model(MobTemplate)
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
        self.assertEqual(exported_docs[-1]["kind"], "world")

        expected_kinds = (
            ["currency"] * resp.data["summary"]["currencies"]
            + ["itemtemplate"] * resp.data["summary"]["item_templates"]
            + ["zone"] * resp.data["summary"]["zones"]
            + ["room"] * resp.data["summary"]["rooms"]
            + ["mobtemplate"] * resp.data["summary"]["mob_templates"]
            + ["questarc"] * resp.data["summary"]["quest_arcs"]
            + ["quest"] * resp.data["summary"]["quests"]
            + ["trigger"] * resp.data["summary"]["triggers"]
            + ["world"]
        )
        self.assertEqual([doc["kind"] for doc in exported_docs], expected_kinds)

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
        self.assertEqual(apply_resp.status_code, 200)
        self.assertEqual(apply_resp.data["kind"], "batch")
        self.assertEqual(apply_resp.data["operation"], "applied")
        self.assertEqual(apply_resp.data["summary"]["documents"], len(source_docs))

        target_export_resp = self.client.get(target_export_ep)
        self.assertEqual(target_export_resp.status_code, 200)
        target_docs = [doc for doc in yaml.safe_load_all(target_export_resp.data["yaml"]) if doc is not None]

        self.assertEqual(source_docs, target_docs)
