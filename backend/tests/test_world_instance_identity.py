import importlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from django.apps import apps as global_apps
from django.core.exceptions import ValidationError
from django.db import (
    IntegrityError,
    close_old_connections,
    connections,
    models,
    transaction,
)
from django.test import TestCase, TransactionTestCase

from worlds.models import World


class WorldInstanceSlugTests(TestCase):

    def setUp(self):
        self.base_world = World.objects.create(
            name="Portable World",
            is_multiplayer=True,
        )

    def test_authored_templates_receive_stable_collision_safe_slugs(self):
        first = World.objects.create(
            name="Trial Hall",
            instance_of=self.base_world,
        )
        second = World.objects.create(
            name="Trial Hall",
            instance_of=self.base_world,
        )
        explicit = World.objects.create(
            name="Different Display Name",
            instance_of=self.base_world,
            instance_slug="custom-identity",
        )

        self.assertEqual(first.instance_slug, "trial-hall")
        self.assertEqual(second.instance_slug, "trial-hall-2")
        self.assertEqual(explicit.instance_slug, "custom-identity")

        first.name = "Renamed Trial Hall"
        first.save(update_fields=["name"])
        first.refresh_from_db()
        self.assertEqual(first.instance_slug, "trial-hall")

    def test_runtime_worlds_do_not_receive_authored_instance_slugs(self):
        template = World.objects.create(
            name="Template",
            instance_of=self.base_world,
        )
        runtime = World.objects.create(
            name="Runtime",
            context=self.base_world,
            instance_of=template,
        )

        self.assertIsNone(runtime.instance_slug)

    def test_nested_authored_instance_templates_are_rejected(self):
        template = World.objects.create(
            name="Template",
            instance_of=self.base_world,
        )

        with self.assertRaisesRegex(
            ValidationError,
            "directly to a base world",
        ):
            World.objects.create(
                name="Nested Template",
                instance_of=template,
            )

    def test_manifest_scope_is_immutable_through_model_and_queryset(self):
        template = World.objects.create(
            name="Template",
            instance_of=self.base_world,
        )
        template.instance_slug = "changed"
        with self.assertRaisesRegex(ValidationError, "immutable"):
            template.save(update_fields=["instance_slug"])

        with self.assertRaisesRegex(ValidationError, "manifest scope"):
            World.objects.filter(pk=template.pk).update(
                instance_slug="changed",
            )
        with self.assertRaisesRegex(ValidationError, "manifest scope"):
            World._base_manager.bulk_update(
                [template],
                ["instance_slug"],
            )

        template.refresh_from_db()
        self.assertEqual(template.instance_slug, "template")

    def test_only_authored_direct_templates_may_have_a_slug(self):
        with self.assertRaisesRegex(
            ValidationError,
            "Only authored instance templates",
        ):
            World.objects.create(
                name="Invalid Base",
                instance_slug="invalid",
            )

        plain_manager = models.Manager()
        plain_manager.model = World
        with self.assertRaises(IntegrityError), transaction.atomic():
            plain_manager.bulk_create([
                World(
                    name="Missing Identity",
                    instance_of=self.base_world,
                    instance_slug=None,
                ),
            ])
        for invalid_slug in ("", "Bad Slug", "double--hyphen"):
            with self.subTest(invalid_slug=invalid_slug):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    plain_manager.bulk_create([
                        World(
                            name="Invalid Identity",
                            instance_of=self.base_world,
                            instance_slug=invalid_slug,
                        ),
                    ])

    def test_slug_backfill_rejects_legacy_nested_templates(self):
        template = World.objects.create(
            name="Template",
            instance_of=self.base_world,
        )
        plain_manager = models.Manager()
        plain_manager.model = World
        plain_manager.bulk_create([
            World(
                name="Legacy Nested Template",
                instance_of=template,
                instance_slug="legacy-nested-template",
            ),
        ])
        migration = importlib.import_module(
            "worlds.migrations.0125_instance_template_manifest_slugs"
        )

        with self.assertRaisesRegex(RuntimeError, "non-base parent"):
            migration.backfill_instance_template_slugs(
                global_apps,
                SimpleNamespace(connection=connections["default"]),
            )


class WorldInstanceSlugDatabaseTests(TransactionTestCase):

    def setUp(self):
        self.base_world = World.objects.create(
            name="Portable World",
            is_multiplayer=True,
        )

    def test_database_rejects_manifest_scope_mutation(self):
        migration = importlib.import_module(
            "worlds.migrations.0125_instance_template_manifest_slugs"
        )
        connection = connections["default"]
        with connection.schema_editor() as schema_editor:
            migration.create_world_manifest_identity_trigger(
                None,
                schema_editor,
            )
        try:
            template = World.objects.create(
                name="Template",
                instance_of=self.base_world,
            )
            plain_manager = models.Manager()
            plain_manager.model = World
            with self.assertRaises(IntegrityError), transaction.atomic():
                plain_manager.filter(pk=template.pk).update(
                    instance_slug="changed",
                )

            template.refresh_from_db()
            self.assertEqual(template.instance_slug, "template")

            with self.assertRaises(IntegrityError), transaction.atomic():
                plain_manager.bulk_create([
                    World(
                        name="Nested Raw Template",
                        instance_of=template,
                        instance_slug="nested-raw-template",
                    ),
                ])
        finally:
            with connection.schema_editor() as schema_editor:
                migration.drop_world_manifest_identity_trigger(
                    None,
                    schema_editor,
                )

    def test_concurrent_templates_allocate_distinct_slugs(self):
        barrier = Barrier(2)

        def create_template(_index):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                template = World.objects.create(
                    name="Trial Hall",
                    instance_of_id=self.base_world.pk,
                )
                return template.instance_slug
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            slugs = list(executor.map(create_template, range(2)))

        self.assertEqual(
            sorted(slugs),
            ["trial-hall", "trial-hall-2"],
        )
