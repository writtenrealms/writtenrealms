from importlib import import_module
from types import SimpleNamespace

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase, override_settings


@override_settings(MIGRATION_MODULES={})
class TriggerUidMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        recorder = MigrationRecorder(connection)
        self.migration_table_existed = recorder.has_table()
        recorder.ensure_schema()
        self.original_migrations = set(recorder.migration_qs.values_list('app', 'name'))
        executor = MigrationExecutor(connection)
        # Fast testing settings build the current schema directly. Establish
        # its matching migration state before exercising the real transition.
        executor.migrate(executor.loader.graph.leaf_nodes(), fake=True)

    def tearDown(self):
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            recorder = MigrationRecorder(connection)
            added = set(recorder.migration_qs.values_list('app', 'name')) - self.original_migrations
            for app_label, migration_name in added:
                recorder.record_unapplied(app_label, migration_name)
            if not self.migration_table_existed:
                with connection.schema_editor() as schema_editor:
                    schema_editor.delete_model(recorder.Migration)
            super().tearDown()

    def test_existing_authored_triggers_get_distinct_persistent_uids(self):
        before = [('builders', '0261_participant_combat_integrity')]
        after = [('builders', '0262_trigger_uid')]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps
        OldWorld = old_apps.get_model('worlds', 'World')
        OldTrigger = old_apps.get_model('builders', 'Trigger')
        world = OldWorld.objects.create(name='Existing authored world')
        OldTrigger.objects.bulk_create([
            OldTrigger(world_id=world.pk, name='Same name', script='/echo -- Preserved.')
            for _ in range(1001)
        ])
        original_ids = set(OldTrigger.objects.values_list('pk', flat=True))

        executor = MigrationExecutor(connection)
        executor.migrate(after)
        new_apps = executor.loader.project_state(after).apps
        Trigger = new_apps.get_model('builders', 'Trigger')
        migrated = dict(Trigger.objects.values_list('pk', 'uid'))
        self.assertEqual(set(migrated), original_ids)
        self.assertEqual(len(set(migrated.values())), 1001)
        self.assertTrue(all(uid is not None for uid in migrated.values()))
        self.assertEqual(Trigger.objects.filter(script='/echo -- Preserved.').count(), 1001)

        migration = import_module('builders.migrations.0262_trigger_uid')
        migration.assign_trigger_uids(new_apps, SimpleNamespace(connection=connection))
        self.assertEqual(dict(Trigger.objects.values_list('pk', 'uid')), migrated)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Trigger.objects.create(world_id=world.pk, uid=next(iter(migrated.values())))
