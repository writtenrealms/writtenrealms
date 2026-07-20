from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase

from builders.models import ItemDefinition
from quests.models import QuestInstance, QuestOfferState, QuestTemplate
from quests.services.engine import (
    QuestRuntimeError,
    accept_template,
    can_start_template,
)
from spawns.models import Player
from worlds.models import World, WorldConfig


class TestQuestAcceptanceConcurrency(TransactionTestCase):
    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_user("quest-race@example.com", "p")
        world_config = WorldConfig.objects.create()
        self.world = World.objects.new_world(
            name="Quest Race World",
            author=user,
            config=world_config,
        )
        spawn_world = self.world.create_spawn_world()
        room = self.world.zones.first().rooms.first()
        self.player = Player.objects.create(
            name="Quest Racer",
            room=room,
            user=user,
            world=spawn_world,
        )
        self.seed_packet = ItemDefinition.objects.create(
            world=self.world,
            name="A packet of barley seeds",
            keywords="packet barley seeds",
        )
        self.template = QuestTemplate.objects.create(
            world=self.world,
            slug="receive-barley-seeds",
            name="Receive Barley Seeds",
            quest_type="quest",
            scope="player",
            status="active",
            repeatability_mode="cooldown",
            repeatability_cooldown_seconds=1200,
            max_active=1,
            discovery_policy={"accept_if": {}},
            slot_schema={},
            graph={
                "steps": [
                    {
                        "id": "resolved",
                        "kind": "resolution",
                        "recap": "Callista entrusts you with barley seeds.",
                    }
                ]
            },
            reward_policy={
                "complete": [
                    {
                        "type": "grant_item",
                        "item_definition": self.seed_packet.slug,
                    }
                ]
            },
        )
        # Remove an unrelated get_or_create race from this acceptance test so
        # both workers exercise the quest eligibility boundary itself.
        QuestOfferState.objects.create(player=self.player, template=self.template)

    def test_simultaneous_cooldown_accepts_resolve_and_reward_only_once(self):
        eligibility_barrier = Barrier(2)

        def synchronized_can_start(player, template):
            allowed = can_start_template(player, template)
            try:
                eligibility_barrier.wait(timeout=0.5)
            except BrokenBarrierError:
                pass
            return allowed

        def accept_in_separate_connection():
            close_old_connections()
            try:
                player = Player.objects.get(pk=self.player.pk)
                template = QuestTemplate.objects.get(pk=self.template.pk)
                accept_template(player, template)
                return "accepted"
            except QuestRuntimeError as exc:
                return exc.code
            finally:
                close_old_connections()

        with patch(
            "quests.services.engine.can_start_template",
            side_effect=synchronized_can_start,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(lambda _: accept_in_separate_connection(), range(2)))

        self.assertCountEqual(outcomes, ["accepted", "cannot_start"])
        self.assertEqual(
            QuestInstance.objects.filter(
                player=self.player,
                template=self.template,
                status="resolved",
                resolution="complete",
            ).count(),
            1,
        )
        self.assertEqual(
            self.player.inventory.filter(definition=self.seed_packet).count(),
            1,
        )
