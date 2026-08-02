import json
from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps as global_apps
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)

from builders.models import (
    RoomAction,
    RoomGetTrigger,
    SpawnEntry,
    SpawnPlan,
    Trigger,
)
from quests.models import QuestTemplate
from worlds.models import (
    DeathRoutingPolicy,
    DeathRoutingRoute,
    Room,
    World,
    WorldConfig,
    WorldState,
    Zone,
)


quest_room_item_migration = import_module(
    "quests.migrations.0007_rename_quest_room_item_description"
)
stable_room_ref_migration = import_module(
    "builders.migrations.0254_canonicalize_authored_room_references"
)
spawn_target_migration = import_module(
    "builders.migrations.0255_spawn_entry_relational_targets"
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


class TestStableRoomReferenceMigrationHelpers(SimpleTestCase):
    def test_rewrites_resolvable_aliases_in_nested_values_only(self):
        value = {
            "room": "room.187",
            "commands": [
                "transfer hero room@10, 4, 0",
                "transfer hero room.999",
                "transfer hero room@99,99,99",
                "transfer hero room@42",
            ],
            "text": "A brass plaque is stamped room.187.",
            "room.187": "JSON keys are not semantic reference fields.",
        }

        rewritten, changed = (
            stable_room_ref_migration._canonicalize_authored_value(
                value,
                database_id_refs={187: "room@5"},
                coordinate_refs={(10, 4, 0): "room@5"},
            )
        )

        self.assertTrue(changed)
        self.assertEqual(rewritten["room"], "room@5")
        self.assertEqual(
            rewritten["commands"],
            [
                "transfer hero room@5",
                "transfer hero room.999",
                "transfer hero room@99,99,99",
                "transfer hero room@42",
            ],
        )
        self.assertEqual(
            rewritten["text"],
            "A brass plaque is stamped room.187.",
        )
        self.assertIn("room.187", rewritten)

    def test_rewrite_is_idempotent_and_ignores_partial_tokens(self):
        value = (
            "room@8 room.187 room.187suffix prefixroom.187 "
            "room@10,4,0,7"
        )

        rewritten, changed = (
            stable_room_ref_migration._canonicalize_authored_value(
                value,
                database_id_refs={187: "room@8"},
                coordinate_refs={(10, 4, 0): "room@8"},
                strategy="command",
            )
        )
        rewritten_again, changed_again = (
            stable_room_ref_migration._canonicalize_authored_value(
                rewritten,
                database_id_refs={187: "room@8"},
                coordinate_refs={(10, 4, 0): "room@8"},
                strategy="command",
            )
        )

        self.assertTrue(changed)
        self.assertEqual(
            rewritten,
            "room@8 room@8 room.187suffix prefixroom.187 room@10,4,0,7",
        )
        self.assertFalse(changed_again)
        self.assertEqual(rewritten_again, rewritten)

    def test_rewrites_bare_database_ids_only_in_proven_room_positions(self):
        value = {
            "room_id": "187",
            "conditions": {
                "all": [
                    {"eq": ["actor.room_id", 187]},
                    {"eq": ["actor.item_id", 187]},
                ],
            },
            "description": "187",
        }

        rewritten, changed = (
            stable_room_ref_migration._canonicalize_authored_value(
                value,
                database_id_refs={187: "room@8"},
                coordinate_refs={},
            )
        )

        self.assertTrue(changed)
        self.assertEqual(rewritten["room_id"], "room@8")
        self.assertEqual(
            rewritten["conditions"]["all"][0]["eq"][1],
            "room@8",
        )
        self.assertEqual(
            rewritten["conditions"]["all"][1]["eq"][1],
            187,
        )
        self.assertEqual(rewritten["description"], "187")


class TestSpawnEntryTargetMigrationHelpers(SimpleTestCase):
    def test_preserves_historical_room_semantics_for_scalar_targets(self):
        cases = (
            "room@8",
            "zone@2",
            "path@4",
            "entry.patrol-leader",
            "Legacy Room Name",
        )

        for target in cases:
            with self.subTest(target=target):
                self.assertEqual(
                    spawn_target_migration._target_kind_and_values(target),
                    ("room", [target]),
                )

    def test_preserves_compatible_legacy_aliases(self):
        self.assertEqual(
            spawn_target_migration._target_kind_and_values({
                "room": "room@8",
                "room_ref": "room.187",
                "name": "A display-only room name",
            }),
            ("room", ["room@8", "room.187"]),
        )
        self.assertEqual(
            spawn_target_migration._target_kind_and_values({
                "entry": "patrol-leader",
                "parent_entry": "entry.patrol-leader",
            }),
            ("entry", ["patrol-leader", "entry.patrol-leader"]),
        )

    def test_rejects_multiple_or_missing_target_families(self):
        for target in ({}, {"room": "room@8", "zone": "zone@2"}):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    spawn_target_migration._target_kind_and_values(target)

    def test_parses_all_supported_room_locator_forms(self):
        cases = (
            ("room@8", ("relative_id", 8)),
            ("room@1,-2,3", ("coordinates", (1, -2, 3))),
            ("room.187", ("id", 187)),
            (187, ("id", 187)),
            ("Legacy Room Name", ("name", "Legacy Room Name")),
        )

        for reference, expected in cases:
            with self.subTest(reference=reference):
                self.assertEqual(
                    spawn_target_migration._room_locator(reference),
                    expected,
                )

    def test_validates_entry_dependency_order_and_activity(self):
        entry = SimpleNamespace(plan_id=1, order=2, is_active=True)
        valid_parent = SimpleNamespace(plan_id=1, order=1, is_active=True)

        spawn_target_migration._validate_entry_dependency(
            entry=entry,
            parent=valid_parent,
        )

        invalid_parents = (
            (SimpleNamespace(plan_id=2, order=1, is_active=True), "different"),
            (SimpleNamespace(plan_id=1, order=2, is_active=True), "lower order"),
            (SimpleNamespace(plan_id=1, order=1, is_active=False), "active entry"),
        )
        for parent, expected_error in invalid_parents:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    spawn_target_migration._validate_entry_dependency(
                        entry=entry,
                        parent=parent,
                    )


@override_settings(MIGRATION_MODULES={})
class TestSpawnEntryTargetSchemaMigration(TransactionTestCase):
    migrate_from = [
        ("builders", "0254_canonicalize_authored_room_references"),
    ]
    migrate_to = [
        ("builders", "0255_spawn_entry_relational_targets"),
    ]

    def setUp(self):
        super().setUp()
        recorder = MigrationRecorder(connection)
        self._migration_table_existed = recorder.has_table()
        recorder.ensure_schema()
        self._original_migrations = set(
            recorder.migration_qs.values_list("app", "name")
        )
        executor = MigrationExecutor(connection)
        # The fast test settings create current tables without running local
        # migrations. Record that matching leaf state before exercising the
        # one real backwards/forwards transition under test.
        executor.migrate(executor.loader.graph.leaf_nodes(), fake=True)

    def tearDown(self):
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            recorder = MigrationRecorder(connection)
            added_migrations = set(
                recorder.migration_qs.values_list("app", "name")
            ) - self._original_migrations
            for app_label, migration_name in added_migrations:
                recorder.record_unapplied(app_label, migration_name)
            if not self._migration_table_existed:
                with connection.schema_editor() as schema_editor:
                    schema_editor.delete_model(recorder.Migration)
            super().tearDown()

    def test_migrates_legacy_json_targets_to_fks_and_back(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        World = old_apps.get_model("worlds", "World")
        Zone = old_apps.get_model("worlds", "Zone")
        Room = old_apps.get_model("worlds", "Room")
        Path = old_apps.get_model("builders", "Path")
        SpawnPlan = old_apps.get_model("builders", "SpawnPlan")
        SpawnEntry = old_apps.get_model("builders", "SpawnEntry")

        world = World.objects.create(name="Legacy spawn targets")
        zone = Zone.objects.create(
            world_id=world.id,
            relative_id=7,
            name="Legacy Zone",
        )
        room = Room.objects.create(
            world_id=world.id,
            zone_id=zone.id,
            relative_id=11,
            name="Legacy Room",
            x=1,
            y=2,
            z=3,
        )
        path = Path.objects.create(
            world_id=world.id,
            zone_id=zone.id,
            relative_id=13,
            name="Legacy Path",
        )
        plan = SpawnPlan.objects.create(
            world_id=world.id,
            zone_id=zone.id,
            slug="legacy-targets",
        )
        room_entry = SpawnEntry.objects.create(
            plan_id=plan.id,
            slug="room-target",
            order=1,
            target={"room": f"room@{room.relative_id}"},
        )
        zone_entry = SpawnEntry.objects.create(
            plan_id=plan.id,
            slug="zone-target",
            order=2,
            target={"zone": f"zone@{zone.relative_id}"},
        )
        path_entry = SpawnEntry.objects.create(
            plan_id=plan.id,
            slug="path-target",
            order=3,
            target={"path": f"path@{path.relative_id}"},
        )
        child_entry = SpawnEntry.objects.create(
            plan_id=plan.id,
            slug="entry-target",
            order=4,
            target={"entry": room_entry.slug},
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        MigratedEntry = new_apps.get_model("builders", "SpawnEntry")
        migrated = {
            entry.slug: entry
            for entry in MigratedEntry.objects.filter(plan_id=plan.id)
        }

        expected_targets = {
            "room-target": (room.id, None, None, None),
            "zone-target": (None, zone.id, None, None),
            "path-target": (None, None, path.id, None),
            "entry-target": (None, None, None, room_entry.id),
        }
        for slug, expected in expected_targets.items():
            entry = migrated[slug]
            actual = (
                entry.target_room_id,
                entry.target_zone_id,
                entry.target_path_id,
                entry.target_entry_id,
            )
            self.assertEqual(actual, expected)
            self.assertEqual(sum(value is not None for value in actual), 1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            MigratedEntry.objects.create(
                plan_id=plan.id,
                slug="missing-target",
            )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        restored_apps = executor.loader.project_state(self.migrate_from).apps
        RestoredEntry = restored_apps.get_model("builders", "SpawnEntry")
        restored = dict(
            RestoredEntry.objects.filter(
                id__in=[
                    room_entry.id,
                    zone_entry.id,
                    path_entry.id,
                    child_entry.id,
                ]
            ).values_list("slug", "target")
        )
        self.assertEqual(
            restored,
            {
                "room-target": {"room": f"room@{room.relative_id}"},
                "zone-target": {"zone": f"zone@{zone.relative_id}"},
                "path-target": {"path": f"path@{path.relative_id}"},
                "entry-target": {"entry": room_entry.slug},
            },
        )


class TestStableRoomReferenceDataMigration(TestCase):
    def test_canonicalizes_authored_fields_but_not_runtime_state(self):
        config = WorldConfig.objects.create(
            ability_progression={
                "starting_abilities": [
                    {
                        "ability": "recall",
                        "conditions": {
                            "eq": ["actor.room_id", "room@12,3,4"],
                        },
                    },
                ],
            },
        )
        world = World.objects.create(
            name="Stable Ref Migration",
            config=config,
        )
        room = Room.objects.create(
            world=world,
            name="Referenced Room",
            x=12,
            y=3,
            z=4,
        )
        other_world = World.objects.create(name="Other World")
        other_room = Room.objects.create(
            world=other_world,
            name="Foreign Room",
            x=12,
            y=3,
            z=4,
        )
        database_ref = f"room.{room.id}"
        coordinate_ref = "room@12,3,4"
        canonical_ref = f"room@{room.relative_id}"
        foreign_ref = f"room.{other_room.id}"

        zone = Zone.objects.create(world=world, name="Migration Zone")
        plan = SpawnPlan.objects.create(
            world=world,
            zone=zone,
            slug="migration-plan",
        )
        entry = SpawnEntry.objects.create(
            plan=plan,
            slug="migration-entry",
            target_room=room,
            conditions={
                "all": [
                    {"eq": ["actor.room_id", database_ref]},
                    {"eq": ["actor.room_id", foreign_ref]},
                ],
            },
        )
        trigger = Trigger.objects.create(
            world=world,
            name="Migration Trigger",
            script=f"/transfer actor {database_ref}",
            steps=[
                {
                    "actions": [
                        {
                            "command": f"/transfer actor {coordinate_ref}",
                        },
                    ],
                },
            ],
            conditions=json.dumps(
                {"eq": ["actor.room_id", database_ref]}
            ),
        )
        quest = QuestTemplate.objects.create(
            world=world,
            slug="migration-quest",
            name="Migration Quest",
            discovery_policy={
                "type": "room_prompt",
                "room": database_ref,
            },
            graph={
                "steps": [
                    {
                        "id": "start",
                        "room_items": [
                            {
                                "id": "marker",
                                "room": coordinate_ref,
                            },
                        ],
                    },
                ],
            },
            reward_policy={
                "complete": [
                    {
                        "type": "actor_command",
                        "command": f"/transfer actor {database_ref}",
                    },
                ],
            },
        )
        room_action = RoomAction.objects.create(
            room=room,
            actions="inspect",
            commands=f"/transfer actor {database_ref}",
            conditions=json.dumps(
                {"eq": ["actor.room_id", coordinate_ref]}
            ),
        )
        get_trigger = RoomGetTrigger.objects.create(
            room=room,
            argument="token",
            action="transport",
            action_argument=database_ref,
        )
        message_get_trigger = RoomGetTrigger.objects.create(
            room=room,
            argument="note",
            action="message",
            action_argument=database_ref,
        )
        death_policy = DeathRoutingPolicy.objects.create(
            config=config,
            enabled=True,
        )
        death_route = DeathRoutingRoute.objects.create(
            policy=death_policy,
            position=0,
            condition={"eq": ["actor.room_id", coordinate_ref]},
            destination_room=room,
        )
        runtime_state = WorldState.objects.create(
            world=world,
            data={"destination": database_ref},
        )
        spawned_world = World.objects.create(
            name="Runtime Spawn World",
            config=config,
            context=world,
        )
        spawned_room = Room.objects.create(
            world=spawned_world,
            name="Runtime Room",
            x=2,
            y=2,
            z=2,
        )
        runtime_trigger = Trigger.objects.create(
            world=spawned_world,
            name="Runtime Trigger Copy",
            script=f"/transfer actor room.{spawned_room.id}",
        )

        schema_editor = SimpleNamespace(connection=connection)
        stable_room_ref_migration.canonicalize_authored_room_references(
            global_apps,
            schema_editor,
        )
        # The forward pass is intentionally retry-safe after partial deployment.
        stable_room_ref_migration.canonicalize_authored_room_references(
            global_apps,
            schema_editor,
        )

        config.refresh_from_db()
        entry.refresh_from_db()
        trigger.refresh_from_db()
        quest.refresh_from_db()
        room_action.refresh_from_db()
        get_trigger.refresh_from_db()
        message_get_trigger.refresh_from_db()
        death_route.refresh_from_db()
        runtime_state.refresh_from_db()
        runtime_trigger.refresh_from_db()

        self.assertEqual(
            config.ability_progression["starting_abilities"][0][
                "conditions"
            ]["eq"][1],
            canonical_ref,
        )
        self.assertEqual(entry.target_room_id, room.id)
        self.assertEqual(
            entry.conditions["all"][0]["eq"][1],
            canonical_ref,
        )
        self.assertEqual(
            entry.conditions["all"][1]["eq"][1],
            foreign_ref,
        )
        self.assertEqual(
            trigger.script,
            f"/transfer actor {canonical_ref}",
        )
        self.assertEqual(
            trigger.steps[0]["actions"][0]["command"],
            f"/transfer actor {canonical_ref}",
        )
        self.assertEqual(
            json.loads(trigger.conditions)["eq"][1],
            canonical_ref,
        )
        self.assertEqual(
            quest.discovery_policy["room"],
            canonical_ref,
        )
        self.assertEqual(
            quest.graph["steps"][0]["room_items"][0]["room"],
            canonical_ref,
        )
        self.assertEqual(
            quest.reward_policy["complete"][0]["command"],
            f"/transfer actor {canonical_ref}",
        )
        self.assertEqual(
            room_action.commands,
            f"/transfer actor {canonical_ref}",
        )
        self.assertEqual(
            json.loads(room_action.conditions)["eq"][1],
            canonical_ref,
        )
        self.assertEqual(get_trigger.action_argument, canonical_ref)
        self.assertEqual(
            message_get_trigger.action_argument,
            database_ref,
        )
        self.assertEqual(
            death_route.condition["eq"][1],
            canonical_ref,
        )
        self.assertEqual(
            runtime_state.data,
            {"destination": database_ref},
        )
        self.assertEqual(
            runtime_trigger.script,
            f"/transfer actor room.{spawned_room.id}",
        )

        room.x = 99
        room.save(update_fields=["x"])
        entry.refresh_from_db()
        self.assertEqual(entry.target_room_id, room.id)
