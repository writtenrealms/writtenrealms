from django.db import migrations


def create_room_identity_insert_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        CREATE FUNCTION worlds_room_enforce_identity_insert()
        RETURNS trigger AS $$
        DECLARE
            next_identity bigint;
        BEGIN
            SELECT next_room_relative_id
              INTO next_identity
              FROM worlds_world
             WHERE id = NEW.world_id
             FOR UPDATE;

            IF next_identity IS NULL THEN
                RAISE EXCEPTION
                    'Room world does not exist'
                    USING ERRCODE = '23503';
            END IF;
            IF NEW.relative_id IS NULL OR NEW.relative_id <= 0 THEN
                RAISE EXCEPTION
                    'Room relative_id must be positive'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.relative_id < next_identity THEN
                RAISE EXCEPTION
                    'Room relative_id was already allocated or retired'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.relative_id >= 9223372036854775807 THEN
                RAISE EXCEPTION
                    'Room relative_id space is exhausted'
                    USING ERRCODE = '23514';
            END IF;

            UPDATE worlds_world
               SET next_room_relative_id = NEW.relative_id + 1
             WHERE id = NEW.world_id;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER worlds_room_identity_insert_guard
        BEFORE INSERT ON worlds_room
        FOR EACH ROW
        EXECUTE FUNCTION worlds_room_enforce_identity_insert();
        """
    )
    schema_editor.execute(
        """
        CREATE FUNCTION worlds_world_reject_room_allocator_rewind()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.next_room_relative_id < OLD.next_room_relative_id THEN
                RAISE EXCEPTION
                    'World room identity allocator cannot be rewound'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER worlds_world_room_allocator_monotonic
        BEFORE UPDATE OF next_room_relative_id ON worlds_world
        FOR EACH ROW
        EXECUTE FUNCTION worlds_world_reject_room_allocator_rewind();
        """
    )


def drop_room_identity_insert_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS worlds_room_identity_insert_guard
        ON worlds_room;
        """
    )
    schema_editor.execute(
        """
        DROP FUNCTION IF EXISTS worlds_room_enforce_identity_insert();
        """
    )
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS worlds_world_room_allocator_monotonic
        ON worlds_world;
        """
    )
    schema_editor.execute(
        """
        DROP FUNCTION IF EXISTS
        worlds_world_reject_room_allocator_rewind();
        """
    )


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0123_stable_room_relative_ids"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="room",
            options={"base_manager_name": "objects"},
        ),
        migrations.RunPython(
            create_room_identity_insert_trigger,
            reverse_code=drop_room_identity_insert_trigger,
        ),
    ]
