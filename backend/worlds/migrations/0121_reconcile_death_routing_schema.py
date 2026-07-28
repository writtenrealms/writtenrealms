from django.db import migrations


LEGACY_DECISION_TABLE = 'worlds_deathroutingdecision'
LEGACY_STATE_KEY_COLUMN = 'death_routing_state_key'

ROUTE_REQUIRED_COLUMNS = {
    'id',
    'created_ts',
    'modified_ts',
    'position',
    'condition',
    'compiled_version',
    'compiled_condition',
    'destination_room_id',
    'policy_id',
}
ROUTE_REQUIRED_CONSTRAINTS = {
    'worlds_death_route_position_unique',
    'worlds_death_route_position_bound',
}
REFERENCE_REQUIRED_COLUMNS = {
    'id',
    'created_ts',
    'modified_ts',
    'snapshot_id',
    'destination_room_id',
    'core_faction_id',
    'origin_zone_id',
}
REFERENCE_REQUIRED_CONSTRAINTS = {
    'worlds_death_snapshot_ref_one_target',
    'worlds_death_snapshot_room_unique',
    'worlds_death_snapshot_faction_unique',
    'worlds_death_snapshot_zone_unique',
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
            'Cannot automatically reconcile deterministic death routing: '
            f'{table_name} contains {row_count} row(s). No schema changes '
            'were applied; preserve and migrate those rows explicitly.'
        )


def _assert_legacy_state_keys_empty(schema_editor, config_table):
    quoted_table = schema_editor.quote_name(config_table)
    quoted_column = schema_editor.quote_name(LEGACY_STATE_KEY_COLUMN)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f'SELECT COUNT(*) FROM {quoted_table} '
            f'WHERE COALESCE({quoted_column}, %s) <> %s',
            ['', ''],
        )
        row_count = cursor.fetchone()[0]
    if row_count:
        raise RuntimeError(
            'Cannot automatically remove the obsolete death-routing state '
            f'key: {row_count} world config row(s) contain a value. No '
            'schema changes were applied; preserve and migrate those values '
            'explicitly.'
        )


def _lock_tables_for_reconciliation(
        schema_editor,
        table_names,
        *,
        first_table=None):
    """
    Hold checked tables through the atomic guard-and-rebuild transaction.

    Without this lock, a live request could insert a row after the empty-table
    guard but before DROP TABLE acquires its own lock.
    """
    remaining_names = set(table_names)
    normalized_names = []
    if first_table in remaining_names:
        normalized_names.append(first_table)
        remaining_names.remove(first_table)
    normalized_names.extend(sorted(remaining_names))
    normalized_names = tuple(normalized_names)
    if not normalized_names:
        return
    if schema_editor.connection.vendor != 'postgresql':
        raise RuntimeError(
            'The stale death-routing schema repair requires PostgreSQL '
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


def reconcile_death_routing_schema(apps, schema_editor):
    """
    Reconcile databases that applied an earlier, uncommitted form of 0119.

    The affected tables held only experimental death-routing derived data.
    Refuse to rebuild them if any rows exist so this repair can never silently
    discard routing data. Authored worlds, rooms, factions, players, and their
    runtime state are outside this rebuild.
    """
    connection = schema_editor.connection
    policy_model = apps.get_model('worlds', 'DeathRoutingPolicy')
    route_model = apps.get_model('worlds', 'DeathRoutingRoute')
    snapshot_model = apps.get_model(
        'worlds',
        'DeathRoutingCompiledSnapshot',
    )
    reference_model = apps.get_model(
        'worlds',
        'DeathRoutingSnapshotReference',
    )
    config_model = apps.get_model('worlds', 'WorldConfig')

    policy_table = policy_model._meta.db_table
    route_table = route_model._meta.db_table
    snapshot_table = snapshot_model._meta.db_table
    reference_table = reference_model._meta.db_table
    config_table = config_model._meta.db_table
    current_tables = (
        policy_table,
        route_table,
        snapshot_table,
        reference_table,
    )

    tables = _table_names(connection)
    config_columns = _columns(connection, config_table)
    route_columns = (
        _columns(connection, route_table)
        if route_table in tables
        else {}
    )
    reference_columns = (
        _columns(connection, reference_table)
        if reference_table in tables
        else {}
    )
    route_constraints = (
        _constraint_names(connection, route_table)
        if route_table in tables
        else set()
    )
    reference_constraints = (
        _constraint_names(connection, reference_table)
        if reference_table in tables
        else set()
    )

    current_shape_complete = (
        set(current_tables).issubset(tables)
        and ROUTE_REQUIRED_COLUMNS.issubset(route_columns)
        and ROUTE_REQUIRED_CONSTRAINTS.issubset(route_constraints)
        and REFERENCE_REQUIRED_COLUMNS.issubset(reference_columns)
        and reference_columns['destination_room_id'].null_ok
        and REFERENCE_REQUIRED_CONSTRAINTS.issubset(
            reference_constraints
        )
    )
    legacy_decision_exists = LEGACY_DECISION_TABLE in tables
    legacy_state_key_exists = LEGACY_STATE_KEY_COLUMN in config_columns

    if (
        current_shape_complete
        and not legacy_decision_exists
        and not legacy_state_key_exists
    ):
        return

    tables_to_lock = set()
    if not current_shape_complete:
        # Recreated routing tables add foreign keys to WorldConfig. Lock it
        # first even in hybrid stale schemas that no longer have the legacy
        # state-key column.
        tables_to_lock.add(config_table)
        tables_to_lock.update(
            table_name
            for table_name in (*current_tables, LEGACY_DECISION_TABLE)
            if table_name in tables
        )
    elif legacy_decision_exists:
        tables_to_lock.add(LEGACY_DECISION_TABLE)
    if legacy_state_key_exists:
        tables_to_lock.add(config_table)
    # Runtime config publication locks WorldConfig before it writes routing
    # rows. Match that order to avoid a migration/request deadlock.
    _lock_tables_for_reconciliation(
        schema_editor,
        tables_to_lock,
        first_table=config_table,
    )

    # Complete every guard before the first DDL statement. PostgreSQL also
    # runs this migration atomically, but preflighting makes the safety
    # boundary explicit on every supported database.
    if not current_shape_complete:
        for table_name in (*current_tables, LEGACY_DECISION_TABLE):
            if table_name in tables:
                _assert_table_empty(schema_editor, table_name)
    elif legacy_decision_exists:
        _assert_table_empty(schema_editor, LEGACY_DECISION_TABLE)

    if legacy_state_key_exists:
        _assert_legacy_state_keys_empty(schema_editor, config_table)

    if not current_shape_complete:
        # Drop dependants before their referenced routing tables.
        for table_name in (
            LEGACY_DECISION_TABLE,
            reference_table,
            route_table,
            snapshot_table,
            policy_table,
        ):
            if table_name in tables:
                _drop_table(schema_editor, table_name)

        schema_editor.create_model(policy_model)
        schema_editor.create_model(route_model)
        schema_editor.create_model(snapshot_model)
        schema_editor.create_model(reference_model)
    elif legacy_decision_exists:
        _drop_table(schema_editor, LEGACY_DECISION_TABLE)

    if legacy_state_key_exists:
        schema_editor.execute(
            f'ALTER TABLE {schema_editor.quote_name(config_table)} '
            f'DROP COLUMN {schema_editor.quote_name(LEGACY_STATE_KEY_COLUMN)}'
        )


class Migration(migrations.Migration):

    atomic = True

    dependencies = [
        ('worlds', '0120_instance_participant_exit_shape'),
    ]

    operations = [
        migrations.RunPython(
            reconcile_death_routing_schema,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
