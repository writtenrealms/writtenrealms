import importlib

from django.apps import apps as global_apps
from django.db import connection
from django.test import TransactionTestCase

from worlds.models import WorldConfig


worlds_repair = importlib.import_module(
    'worlds.migrations.0121_reconcile_death_routing_schema'
)
spawns_repair = importlib.import_module(
    'spawns.migrations.0154_reconcile_death_receipt_schema'
)


def _table_names():
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


def _columns(table_name):
    with connection.cursor() as cursor:
        return {
            column.name: column
            for column in connection.introspection.get_table_description(
                cursor,
                table_name,
            )
        }


def _constraint_names(table_name):
    with connection.cursor() as cursor:
        return set(
            connection.introspection.get_constraints(cursor, table_name)
        )


class DeathRoutingSchemaRepairTests(TransactionTestCase):
    reset_sequences = False

    def _run_worlds_repair(self):
        with connection.schema_editor() as schema_editor:
            worlds_repair.reconcile_death_routing_schema(
                global_apps,
                schema_editor,
            )

    def _run_spawns_repair(self):
        with connection.schema_editor() as schema_editor:
            spawns_repair.reconcile_death_receipt_schema(
                global_apps,
                schema_editor,
            )

    def test_worlds_repair_rebuilds_stale_empty_schema_and_preserves_config(self):
        config = WorldConfig.objects.create()
        config_id = config.id
        route_table = 'worlds_deathroutingroute'
        config_table = 'worlds_worldconfig'

        with connection.schema_editor() as schema_editor:
            schema_editor.execute(
                f'DROP TABLE {schema_editor.quote_name(route_table)}'
            )
            schema_editor.execute(
                f'ALTER TABLE {schema_editor.quote_name(config_table)} '
                'ADD COLUMN "death_routing_state_key" '
                "varchar(64) NOT NULL DEFAULT ''"
            )
            schema_editor.execute(
                f'ALTER TABLE {schema_editor.quote_name(config_table)} '
                'ALTER COLUMN "death_routing_state_key" DROP DEFAULT'
            )

        self._run_worlds_repair()
        # A second run proves the current-schema path is idempotent.
        self._run_worlds_repair()

        self.assertTrue(WorldConfig.objects.filter(pk=config_id).exists())
        self.assertIn(route_table, _table_names())
        self.assertNotIn(
            'death_routing_state_key',
            _columns(config_table),
        )

        route_columns = _columns(route_table)
        self.assertTrue(
            worlds_repair.ROUTE_REQUIRED_COLUMNS.issubset(route_columns)
        )
        self.assertTrue(
            worlds_repair.ROUTE_REQUIRED_CONSTRAINTS.issubset(
                _constraint_names(route_table)
            )
        )

        reference_table = 'worlds_deathroutingsnapshotreference'
        reference_columns = _columns(reference_table)
        self.assertTrue(
            worlds_repair.REFERENCE_REQUIRED_COLUMNS.issubset(
                reference_columns
            )
        )
        self.assertTrue(reference_columns['destination_room_id'].null_ok)
        self.assertTrue(
            worlds_repair.REFERENCE_REQUIRED_CONSTRAINTS.issubset(
                _constraint_names(reference_table)
            )
        )

    def test_worlds_repair_aborts_before_ddl_for_nonempty_legacy_state_key(self):
        route_table = 'worlds_deathroutingroute'
        config_table = 'worlds_worldconfig'
        WorldConfig.objects.create()
        with connection.schema_editor() as schema_editor:
            schema_editor.execute(
                f'ALTER TABLE {schema_editor.quote_name(config_table)} '
                'ADD COLUMN "death_routing_state_key" '
                "varchar(64) NOT NULL DEFAULT 'legacy.key'"
            )
            schema_editor.execute(
                f'ALTER TABLE {schema_editor.quote_name(config_table)} '
                'ALTER COLUMN "death_routing_state_key" DROP DEFAULT'
            )

        try:
            with self.assertRaisesMessage(
                RuntimeError,
                'contain a value',
            ):
                self._run_worlds_repair()
            self.assertIn(route_table, _table_names())
            self.assertIn(
                'death_routing_state_key',
                _columns(config_table),
            )
        finally:
            with connection.schema_editor() as schema_editor:
                schema_editor.execute(
                    f'ALTER TABLE {schema_editor.quote_name(config_table)} '
                    'DROP COLUMN "death_routing_state_key"'
                )

    def test_spawns_repair_rebuilds_stale_empty_schema(self):
        receipt_table = 'spawns_deathresolutionreceipt'
        choice_table = 'spawns_deathroutechoicereceipt'

        with connection.schema_editor() as schema_editor:
            schema_editor.execute(
                f'DROP TABLE {schema_editor.quote_name(receipt_table)}'
            )
            schema_editor.execute(
                f'CREATE TABLE {schema_editor.quote_name(choice_table)} '
                '(id bigint PRIMARY KEY)'
            )

        self._run_spawns_repair()
        # A second run proves the current-schema path is idempotent.
        self._run_spawns_repair()

        self.assertIn(receipt_table, _table_names())
        self.assertNotIn(choice_table, _table_names())
        receipt_columns = _columns(receipt_table)
        self.assertTrue(
            spawns_repair.RECEIPT_REQUIRED_COLUMNS.issubset(
                receipt_columns
            )
        )
        self.assertFalse(
            spawns_repair.LEGACY_RECEIPT_COLUMNS.intersection(
                receipt_columns
            )
        )
        self.assertTrue(all(
            receipt_columns[column_name].null_ok
            for column_name in spawns_repair.RECEIPT_NULLABLE_COLUMNS
        ))
        self.assertTrue(
            spawns_repair.RECEIPT_REQUIRED_CONSTRAINTS.issubset(
                _constraint_names(receipt_table)
            )
        )

    def test_spawns_repair_aborts_before_dropping_legacy_receipts(self):
        choice_table = 'spawns_deathroutechoicereceipt'
        with connection.schema_editor() as schema_editor:
            schema_editor.execute(
                f'CREATE TABLE {schema_editor.quote_name(choice_table)} '
                '(id bigint PRIMARY KEY)'
            )
            schema_editor.execute(
                f'INSERT INTO {schema_editor.quote_name(choice_table)} '
                'VALUES (1)'
            )

        try:
            with self.assertRaisesMessage(
                RuntimeError,
                'contains 1 row(s)',
            ):
                self._run_spawns_repair()
            self.assertIn(choice_table, _table_names())
        finally:
            with connection.schema_editor() as schema_editor:
                schema_editor.execute(
                    f'DROP TABLE {schema_editor.quote_name(choice_table)}'
                )
