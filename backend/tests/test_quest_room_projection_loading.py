from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from builders.models import ItemDefinition
from config import constants as adv_consts
from quests.models import QuestInstance, QuestJournalEntry, QuestObjectiveState, QuestTemplate
from quests.services.room_items import claim_quest_room_item, quest_room_item_projections_for_room
from tests.base import WorldTestCase
from worlds.models import World, WorldConfig
from worlds.room_refs import format_room_manifest_ref


class QuestRoomProjectionLoadingTests(WorldTestCase):
    def test_pickups_keep_run_scope_order_and_content_without_loading_history(self):
        template_world = World.objects.new_world(
            name="Pickup instance", author=self.user, instance_of=self.world,
            config=WorldConfig.objects.create(),
        )
        runtime = template_world.create_spawn_world(instance_ref="pickup-current")
        other_runtime = template_world.create_spawn_world(instance_ref="pickup-other")
        room = template_world.config.starting_room
        self.player.world = runtime
        self.player.room = room
        self.player.save(update_fields=["world", "room"])
        definition = ItemDefinition.objects.create(
            world=self.world, slug="sealed-note", name="a sealed note",
            description="A note addressed to the traveller.", keywords="sealed note",
            item_type=adv_consts.ITEM_TYPE_QUEST,
        )
        instances = []
        for index in range(2):
            template = QuestTemplate.objects.create(
                world=template_world, slug=f"pickup-{index}", name=f"Pickup {index}",
                status="active", graph={"steps": [{
                    "id": "find", "kind": "objective", "room_items": [{
                        "id": "note", "room": format_room_manifest_ref(room),
                        "item_definition": definition.slug,
                        "room_description": "A sealed note rests nearby.",
                    }],
                }]},
            )
            instance = QuestInstance.objects.create(
                world=runtime, player=self.player, template=template, current_step_id="find",
            )
            QuestInstance.objects.filter(pk=instance.pk).update(
                modified_ts=timezone.now() + timedelta(seconds=index),
            )
            instances.append(instance)
            QuestObjectiveState.objects.bulk_create([
                QuestObjectiveState(quest_instance=instance, objective_id=f"goal-{number}")
                for number in range(20)
            ])
            QuestJournalEntry.objects.bulk_create([
                QuestJournalEntry(quest_instance=instance, recap=f"History {number}")
                for number in range(20)
            ])
        for world, status in ((other_runtime, "active"), (runtime, "resolved")):
            QuestInstance.objects.create(
                world=world, player=self.player, template=template,
                current_step_id="find", status=status,
            )

        with CaptureQueriesContext(connection) as queries:
            projections = quest_room_item_projections_for_room(self.player, room.pk)

        expected = [{
            "key": f"questroomitem.{instance.pk}.find.note",
            "name": "a sealed note", "cf_name": "A sealed note",
            "type": adv_consts.ITEM_TYPE_QUEST,
            "description": definition.description,
            "room_description": "A sealed note rests nearby.",
            "definition": definition.slug, "definition_id": None,
            "definition_slug": definition.slug, "is_pickable": True,
            "keywords": "sealed note", "keyword": "sealed", "indicator": "*",
        } for instance in reversed(instances)]
        self.assertEqual([projection.to_item_payload() for projection in projections], expected)
        sql = "\n".join(query["sql"] for query in queries)
        for unused_table in (
            '"quests_questjournalentry"', '"quests_questobjectivestate"',
            '"quests_questarc"', '"spawns_player"',
        ):
            self.assertNotIn(unused_table, sql)

        claimed = claim_quest_room_item(self.player, projections[0])
        self.assertEqual(claimed.definition_id, definition.pk)
        remaining = quest_room_item_projections_for_room(self.player, room.pk)
        self.assertEqual([projection.to_item_payload() for projection in remaining], expected[1:])
        self.assertEqual(QuestJournalEntry.objects.count(), 40)
        self.assertEqual(QuestObjectiveState.objects.count(), 40)
