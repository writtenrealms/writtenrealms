from copy import deepcopy
from importlib import import_module

from django.test import SimpleTestCase


quest_room_item_migration = import_module(
    "quests.migrations.0007_rename_quest_room_item_description"
)


class TestQuestRoomItemDescriptionMigration(SimpleTestCase):
    def test_rename_room_item_key_preserves_description(self):
        graph = {
            "steps": [
                {
                    "room_items": [
                        {
                            "id": "keg",
                            "ground_description": "A full keg rests here.",
                        }
                    ]
                }
            ]
        }

        changed = quest_room_item_migration._rename_room_item_key(
            graph,
            "ground_description",
            "room_description",
        )

        self.assertTrue(changed)
        self.assertEqual(
            graph["steps"][0]["room_items"][0],
            {
                "id": "keg",
                "room_description": "A full keg rests here.",
            },
        )

    def test_rename_room_item_key_preserves_pre_migration_behavior_on_collision(self):
        graph = {
            "steps": [
                {
                    "room_items": [
                        {
                            "ground_description": "The active old value.",
                            "room_description": "An ignored extra value.",
                        }
                    ]
                }
            ]
        }

        quest_room_item_migration._rename_room_item_key(
            graph,
            "ground_description",
            "room_description",
        )

        self.assertEqual(
            graph["steps"][0]["room_items"][0],
            {"room_description": "The active old value."},
        )

    def test_rename_room_item_key_ignores_malformed_graphs(self):
        malformed_graphs = [
            None,
            [],
            {},
            {"steps": "not-a-list"},
            {"steps": 3},
            {
                "steps": [
                    None,
                    {"room_items": "not-a-list"},
                    {"room_items": 3},
                ]
            },
        ]

        for graph in malformed_graphs:
            with self.subTest(graph=graph):
                original = deepcopy(graph)
                changed = quest_room_item_migration._rename_room_item_key(
                    graph,
                    "ground_description",
                    "room_description",
                )
                self.assertFalse(changed)
                self.assertEqual(graph, original)

    def test_rename_room_item_key_is_reversible(self):
        graph = {
            "steps": [
                {
                    "id": "fetch",
                    "room_items": [
                        {
                            "id": "keg",
                            "ground_description": "A full keg rests here.",
                        }
                    ],
                }
            ]
        }
        original = deepcopy(graph)

        quest_room_item_migration._rename_room_item_key(
            graph,
            "ground_description",
            "room_description",
        )
        quest_room_item_migration._rename_room_item_key(
            graph,
            "room_description",
            "ground_description",
        )

        self.assertEqual(graph, original)
