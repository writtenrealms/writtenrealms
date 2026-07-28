from django.db import migrations


LEGACY_CHOICE_RECEIPT_TABLE = 'spawns_deathroutechoicereceipt'
LEGACY_RECEIPT_COLUMNS = {
    'state_value',
    'state_is_unset',
}
RECEIPT_REQUIRED_COLUMNS = {
    'id',
    'created_ts',
    'modified_ts',
    'player_id',
    'death_token',
    'request_fingerprint',
    'origin_world_id',
    'origin_room_id',
    'destination_world_id',
    'destination_room_id',
    'origin_instance_run_id',
    'origin_instance_participant_id',
    'routing_source',
    'origin_config_id',
    'source_generation',
    'plan_config_id',
    'plan_generation',
    'matched_route_position',
    'core_faction_id',
    'decision_reason',
    'fallback_reason',
    'death_sequence',
    'location_sequence',
    'penalty',
    'corpse_id',
    'result',
}
RECEIPT_NULLABLE_COLUMNS = {
    'origin_world_id',
    'origin_room_id',
    'destination_world_id',
    'destination_room_id',
    'origin_instance_run_id',
    'origin_instance_participant_id',
    'origin_config_id',
    'plan_config_id',
    'matched_route_position',
    'core_faction_id',
    'corpse_id',
}
RECEIPT_REQUIRED_CONSTRAINTS = {
    'spawns_death_receipt_player_token',
}


def _table_names(connection):
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


def _columns(connection, table_name):
    with connection.cursor() as cursor:
        return {
            column.name: column
            for column in connection.introspection.get_table_description(
                cursor,
                table_name,
            )
        }


def _constraint_names(connection, table_name):
    with connection.cursor() as cursor:
        return set(
            connection.introspection.get_constraints(
                cursor,
                table_name,
            )
        )


def _assert_table_empty(schema_editor, table_name):
    quoted_table = schema_editor.quote_name(table_name)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f'SELECT COUNT(*) FROM {quoted_table}')
        row_count = cursor.fetchone()[0]
    if row_count:
        raise RuntimeError(
            'Cannot automatically reconcile deterministic death receipts: '
            f'{table_name} contains {row_count} row(s). No schema changes '
            'were applied; preserve and migrate those rows explicitly.'
        )


def _lock_tables_for_reconciliation(schema_editor, table_names):
    """
    Hold checked tables through the atomic guard-and-rebuild transaction.

    Without this lock, a live request could insert a row after the empty-table
    guard but before DROP TABLE acquires its own lock.
    """
    normalized_names = tuple(sorted(set(table_names)))
    if not normalized_names:
        return
    if schema_editor.connection.vendor != 'postgresql':
        raise RuntimeError(
            'The stale death-receipt schema repair requires PostgreSQL '
            'transactional table locks. No schema changes were applied.'
        )
    quoted_tables = ', '.join(
        schema_editor.quote_name(table_name)
        for table_name in normalized_names
    )
    schema_editor.execute(
        f'LOCK TABLE {quoted_tables} IN ACCESS EXCLUSIVE MODE'
    )


def _drop_table(schema_editor, table_name):
    schema_editor.execute(
        f'DROP TABLE {schema_editor.quote_name(table_name)}'
    )


def reconcile_death_receipt_schema(apps, schema_editor):
    """
    Reconcile databases that applied an earlier, uncommitted form of 0153.

    Only an empty experimental receipt table is rebuilt. Any legacy receipt
    rows cause the migration to abort before DDL rather than risk data loss.
    """
    connection = schema_editor.connection
    receipt_model = apps.get_model('spawns', 'DeathResolutionReceipt')
    receipt_table = receipt_model._meta.db_table

    tables = _table_names(connection)
    receipt_columns = (
        _columns(connection, receipt_table)
        if receipt_table in tables
        else {}
    )
    receipt_constraints = (
        _constraint_names(connection, receipt_table)
        if receipt_table in tables
        else set()
    )
    current_shape_complete = (
        receipt_table in tables
        and RECEIPT_REQUIRED_COLUMNS.issubset(receipt_columns)
        and not LEGACY_RECEIPT_COLUMNS.intersection(receipt_columns)
        and all(
            receipt_columns[column_name].null_ok
            for column_name in RECEIPT_NULLABLE_COLUMNS
        )
        and RECEIPT_REQUIRED_CONSTRAINTS.issubset(receipt_constraints)
    )
    legacy_choice_receipt_exists = LEGACY_CHOICE_RECEIPT_TABLE in tables

    if current_shape_complete and not legacy_choice_receipt_exists:
        return

    tables_to_lock = set()
    if not current_shape_complete and receipt_table in tables:
        tables_to_lock.add(receipt_table)
    if legacy_choice_receipt_exists:
        tables_to_lock.add(LEGACY_CHOICE_RECEIPT_TABLE)
    _lock_tables_for_reconciliation(schema_editor, tables_to_lock)

    # Complete every guard before the first DDL statement.
    if not current_shape_complete and receipt_table in tables:
        _assert_table_empty(schema_editor, receipt_table)
    if legacy_choice_receipt_exists:
        _assert_table_empty(schema_editor, LEGACY_CHOICE_RECEIPT_TABLE)

    if legacy_choice_receipt_exists:
        _drop_table(schema_editor, LEGACY_CHOICE_RECEIPT_TABLE)

    if not current_shape_complete:
        if receipt_table in tables:
            _drop_table(schema_editor, receipt_table)
        schema_editor.create_model(receipt_model)


class Migration(migrations.Migration):

    atomic = True

    dependencies = [
        ('spawns', '0153_player_death_routing_identity_and_receipts'),
        ('worlds', '0121_reconcile_death_routing_schema'),
    ]

    operations = [
        migrations.RunPython(
            reconcile_death_receipt_schema,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
