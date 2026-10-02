"""Read projections avoid unrelated history, actor, and inventory work."""

from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from builders.models import ItemDefinition
from config import constants as adv_consts
from quests.models import QuestInstance, QuestOfferState, QuestTemplate
from quests.services.discovery import (
    available_room_prompt_opportunities_for_room,
    room_prompt_callouts_for_room,
)
from quests.services.engine import can_start_template
from quests.services.room_items import quest_room_item_projections_for_room
from spawns.actions.effects import active_character_effects, active_combat_effects
from spawns.models import ActiveEffect, Item, Player
from tests.base import WorldTestCase
from tests.combat_fixtures import create_combat_encounter
from worlds.room_refs import format_room_manifest_ref


class MovementProjectionQueryTests(WorldTestCase):
    def _quest(self, slug, *, sources=None, steps=None, **kwargs):
        return QuestTemplate.objects.create(
            world=self.world,
            slug=slug,
            name=slug,
            status="active",
            scope="player",
            quest_type="quest",
            discovery_policy={"sources": sources or [], "visible_if": {}},
            graph={"steps": steps or [{"id": "offer", "kind": "storylet", "recap": "A task awaits."}]},
            **kwargs,
        )

    def test_character_effect_payload_uses_references_without_actor_joins(self):
        mob = self.create_mob("Effect source")
        for values in (
            {"source_player": self.player},
            {"source_mob": mob},
            {"source_snapshot": {"ref": {"type": "world", "id": self.world.pk}}},
        ):
            ActiveEffect.objects.create(
                world=self.spawn_world,
                target_player=self.player,
                effect="blessing",
                label="Blessing",
                primitives=[{"type": "stat_modifier", "stat": "health_max", "amount": 4}],
                **values,
            )
        with CaptureQueriesContext(connection) as queries:
            payloads = active_character_effects(self.player)
        self.assertEqual(len(queries), 1)
        self.assertNotIn(" JOIN ", queries[0]["sql"])
        self.assertEqual([row["source"] for row in payloads], [
            {"type": "player", "id": self.player.pk},
            {"type": "mob", "id": mob.pk},
            {"type": "world", "id": self.world.pk},
        ])
        self.assertTrue(all(row["target"] == {"type": "player", "id": self.player.pk} for row in payloads))

    def test_encounter_effects_keep_authoritative_spatial_filtering(self):
        mob = self.create_mob("Effect source")
        encounter = create_combat_encounter(
            world=self.spawn_world, room=self.room, player=self.player, mob=mob,
        )
        effect = ActiveEffect.objects.create(
            world=self.spawn_world,
            encounter=encounter,
            scope=ActiveEffect.SCOPE_ENCOUNTER,
            source_mob=mob,
            target_player=self.player,
            effect="root",
            label="Rooted",
        )
        with self.assertNumQueries(1):
            payloads = active_combat_effects(self.player)
        self.assertEqual([row["id"] for row in payloads], [effect.pk])
        self.assertEqual(payloads[0]["encounter_id"], encounter.pk)
        other_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        Player.objects.filter(pk=self.player.pk).update(room=other_room)
        # Keep the caller's object stale: filtering must still use DB location.
        self.assertEqual(active_combat_effects(self.player), [])

    def test_cooldown_checks_latest_timestamp_without_loading_quest_history(self):
        quest = self._quest(
            "repeatable", repeatability_mode="cooldown", repeatability_cooldown_seconds=60,
        )
        now = timezone.now()
        for resolved_at in (now - timedelta(seconds=120), now - timedelta(seconds=30)):
            QuestInstance.objects.create(
                world=self.spawn_world, player=self.player, template=quest,
                status="resolved", resolution="complete", resolved_at=resolved_at,
            )
        with patch("spawns.instance_clock.gameplay_now", return_value=now):
            with CaptureQueriesContext(connection) as queries:
                self.assertFalse(can_start_template(self.player, quest))
        self.assertFalse(any("quests_questjournalentry" in q["sql"] or "quests_questobjectivestate" in q["sql"] for q in queries))
        with patch("spawns.instance_clock.gameplay_now", return_value=now + timedelta(seconds=30)):
            self.assertTrue(can_start_template(self.player, quest))

    def test_unrelated_discovery_sources_do_not_add_room_eligibility_reads(self):
        quest = self._quest("room-quest", sources=[{
            "type": "room_prompt", "room": format_room_manifest_ref(self.room),
            "callout": "A task awaits.",
        }])
        def read_room():
            with CaptureQueriesContext(connection) as queries:
                callouts = room_prompt_callouts_for_room(self.player, self.room.pk)
                opportunities = available_room_prompt_opportunities_for_room(self.player, self.room.pk)
            return callouts, opportunities, len(queries)

        read_room()
        initial_callouts, initial_opportunities, initial_count = read_room()
        for index in range(8):
            self._quest(f"unrelated-{index}", sources=[{"type": "auto_start"}])
        callouts, opportunities, query_count = read_room()
        self.assertEqual(callouts, initial_callouts)
        self.assertEqual(opportunities, initial_opportunities)
        self.assertEqual(query_count, initial_count)
        self.assertEqual(callouts[0]["slug"], quest.slug)
        QuestOfferState.objects.create(
            player=self.player, template=quest,
            snoozed_until=timezone.now() + timedelta(hours=1),
        )
        self.assertEqual(read_room()[:2], ([], []))

    def test_room_items_skip_inventory_tree_until_matching_claim_needs_it(self):
        other_room = self.room.create_at(adv_consts.DIRECTION_EAST)
        definition = ItemDefinition.objects.create(
            world=self.world, slug="lost-note", name="Lost note",
            item_type=adv_consts.ITEM_TYPE_QUEST,
        )
        quest = self._quest("room-items", steps=[{
            "id": "find", "kind": "objective", "room_items": [{
                "id": "note", "room": format_room_manifest_ref(self.room),
                "item_definition": definition.slug,
            }],
        }])
        instance = QuestInstance.objects.create(
            world=self.spawn_world, player=self.player, template=quest,
            current_step_id="find",
        )
        with patch("quests.services.room_items._player_owned_item_ids", side_effect=AssertionError("unnecessary inventory traversal")):
            self.assertEqual(quest_room_item_projections_for_room(self.player, other_room.pk), [])
            self.assertEqual(len(quest_room_item_projections_for_room(self.player, self.room.pk)), 1)

        bag = Item.objects.create(world=self.spawn_world, name="Bag", type=adv_consts.ITEM_TYPE_CONTAINER, container=self.player)
        claimed = Item.objects.create(world=self.spawn_world, name="Lost note", definition=definition, container=bag)
        instance.local_state = {"room_item_claims": {"find:note": [claimed.pk]}}
        instance.save(update_fields=["local_state"])
        self.assertEqual(quest_room_item_projections_for_room(self.player, self.room.pk), [])
        claimed.delete()
        self.assertEqual(len(quest_room_item_projections_for_room(self.player, self.room.pk)), 1)
