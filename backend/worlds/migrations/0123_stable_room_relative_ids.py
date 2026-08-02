from django.db import migrations, models


MAX_BIGINT = 9_223_372_036_854_775_807


def backfill_room_relative_ids(apps, schema_editor):
    Room = apps.get_model('worlds', 'Room')
    World = apps.get_model('worlds', 'World')
    database = schema_editor.connection.alias

    highest_by_world = {}
    invalid_room_ids_by_world = {}
    room_rows = (
        Room.objects.using(database)
        .order_by('world_id', 'id')
        .values_list('id', 'world_id', 'relative_id')
        .iterator(chunk_size=2000)
    )
    for room_id, world_id, relative_id in room_rows:
        if relative_id is not None and relative_id > 0:
            highest_by_world[world_id] = max(
                highest_by_world.get(world_id, 0),
                relative_id,
            )
        else:
            invalid_room_ids_by_world.setdefault(world_id, []).append(room_id)

    for world_id, room_ids in invalid_room_ids_by_world.items():
        next_relative_id = highest_by_world.get(world_id, 0) + 1
        for room_id in room_ids:
            if next_relative_id > MAX_BIGINT:
                raise RuntimeError(
                    f'Room relative ID space is exhausted for world {world_id}.')
            Room.objects.using(database).filter(pk=room_id).update(
                relative_id=next_relative_id,
            )
            highest_by_world[world_id] = next_relative_id
            next_relative_id += 1

    for world_id, highest_relative_id in highest_by_world.items():
        if highest_relative_id >= MAX_BIGINT:
            raise RuntimeError(
                f'Room relative ID space is exhausted for world {world_id}.')
        World.objects.using(database).filter(pk=world_id).update(
            next_room_relative_id=highest_relative_id + 1,
        )


def create_room_identity_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        """
        CREATE FUNCTION worlds_room_reject_identity_update()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.world_id IS DISTINCT FROM OLD.world_id
               OR NEW.relative_id IS DISTINCT FROM OLD.relative_id THEN
                RAISE EXCEPTION
                    'Room world and relative_id are immutable after creation'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER worlds_room_identity_immutable
        BEFORE UPDATE OF world_id, relative_id ON worlds_room
        FOR EACH ROW
        EXECUTE FUNCTION worlds_room_reject_identity_update();
        """
    )


def drop_room_identity_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS worlds_room_identity_immutable ON worlds_room;
        """
    )
    schema_editor.execute(
        """
        DROP FUNCTION IF EXISTS worlds_room_reject_identity_update();
        """
    )


class Migration(migrations.Migration):

    dependencies = [
        ('worlds', '0122_canonical_doorways'),
    ]

    operations = [
        migrations.AddField(
            model_name='world',
            name='next_room_relative_id',
            field=models.PositiveBigIntegerField(default=1, editable=False),
        ),
        migrations.AlterField(
            model_name='room',
            name='relative_id',
            # Keep the transitional field unconstrained so legacy zero or
            # negative values can be repaired before the positive constraint
            # is installed below.
            field=models.BigIntegerField(
                blank=True,
                editable=False,
                null=True,
            ),
        ),
        migrations.RunPython(
            backfill_room_relative_ids,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='room',
            name='relative_id',
            field=models.PositiveBigIntegerField(editable=False),
        ),
        migrations.AddConstraint(
            model_name='room',
            constraint=models.CheckConstraint(
                condition=models.Q(relative_id__gt=0),
                name='worlds_room_relative_id_positive',
            ),
        ),
        migrations.RunPython(
            create_room_identity_trigger,
            reverse_code=drop_room_identity_trigger,
        ),
    ]
