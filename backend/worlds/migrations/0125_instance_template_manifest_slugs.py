from django.db import migrations, models
from django.db.models import Q
from django.utils.text import slugify


INSTANCE_SLUG_DB_PATTERN = (
    r"^([a-z0-9]|[a-z0-9][a-z0-9_-]*[a-z0-9])$"
)


def backfill_instance_template_slugs(apps, schema_editor):
    World = apps.get_model("worlds", "World")
    database = schema_editor.connection.alias
    invalid_template = (
        World.objects.using(database)
        .filter(
            context__isnull=True,
            instance_of__isnull=False,
        )
        .filter(
            Q(instance_of__context__isnull=False)
            | Q(instance_of__instance_of__isnull=False)
        )
        .values_list("id", "instance_of_id")
        .first()
    )
    if invalid_template is not None:
        world_id, parent_id = invalid_template
        raise RuntimeError(
            "Cannot assign portable instance slugs: authored instance "
            f"template world {world_id} has non-base parent {parent_id}."
        )
    base_world_ids = (
        World.objects.using(database)
        .filter(
            context__isnull=True,
            instance_of__isnull=False,
        )
        .order_by("instance_of_id")
        .values_list("instance_of_id", flat=True)
        .distinct()
    )
    for base_world_id in base_world_ids.iterator(chunk_size=500):
        used = set()
        templates = (
            World.objects.using(database)
            .filter(
                context__isnull=True,
                instance_of_id=base_world_id,
            )
            .order_by("id")
        )
        for template in templates.iterator(chunk_size=500):
            base_slug = slugify(template.name or "")[:100] or "instance"
            candidate = base_slug
            suffix = 2
            while candidate in used:
                suffix_text = f"-{suffix}"
                candidate = (
                    f"{base_slug[:120 - len(suffix_text)]}{suffix_text}"
                )
                suffix += 1
            used.add(candidate)
            World.objects.using(database).filter(pk=template.pk).update(
                instance_slug=candidate,
            )


def create_world_manifest_identity_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        CREATE FUNCTION worlds_world_validate_manifest_identity_insert()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.context_id IS NULL AND NEW.instance_of_id IS NOT NULL THEN
                IF NEW.instance_slug IS NULL
                   OR NEW.instance_slug !~
                       '^([a-z0-9]|[a-z0-9][a-z0-9_-]*[a-z0-9])$'
                   OR POSITION('--' IN NEW.instance_slug) > 0 THEN
                    RAISE EXCEPTION
                        'Authored instance templates require a canonical slug'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM worlds_world parent
                    WHERE parent.id = NEW.instance_of_id
                      AND parent.context_id IS NULL
                      AND parent.instance_of_id IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Authored instance templates must belong directly to a base world'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.instance_slug IS NOT NULL THEN
                RAISE EXCEPTION
                    'Only authored instance templates may have a slug'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER worlds_world_manifest_identity_valid_insert
        BEFORE INSERT
        ON worlds_world
        FOR EACH ROW
        EXECUTE FUNCTION worlds_world_validate_manifest_identity_insert();
        """
    )
    schema_editor.execute(
        """
        CREATE FUNCTION worlds_world_reject_manifest_identity_update()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.instance_of_id IS DISTINCT FROM OLD.instance_of_id
               OR NEW.context_id IS DISTINCT FROM OLD.context_id
               OR NEW.instance_slug IS DISTINCT FROM OLD.instance_slug THEN
                RAISE EXCEPTION
                    'World manifest scope is immutable after creation'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER worlds_world_manifest_identity_immutable
        BEFORE UPDATE OF instance_of_id, context_id, instance_slug
        ON worlds_world
        FOR EACH ROW
        EXECUTE FUNCTION worlds_world_reject_manifest_identity_update();
        """
    )


def drop_world_manifest_identity_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS worlds_world_manifest_identity_valid_insert
        ON worlds_world;
        """
    )
    schema_editor.execute(
        """
        DROP TRIGGER IF EXISTS worlds_world_manifest_identity_immutable
        ON worlds_world;
        """
    )
    schema_editor.execute(
        """
        DROP FUNCTION IF EXISTS
        worlds_world_reject_manifest_identity_update();
        """
    )
    schema_editor.execute(
        """
        DROP FUNCTION IF EXISTS
        worlds_world_validate_manifest_identity_insert();
        """
    )


class Migration(migrations.Migration):

    dependencies = [
        ("worlds", "0124_enforce_room_identity_inserts"),
    ]

    operations = [
        migrations.AddField(
            model_name="world",
            name="instance_slug",
            field=models.SlugField(
                blank=True,
                editable=False,
                max_length=120,
                null=True,
            ),
        ),
        migrations.RunPython(
            backfill_instance_template_slugs,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterModelOptions(
            name="world",
            options={
                "base_manager_name": "objects",
                "ordering": ("-created_ts",),
            },
        ),
        migrations.AddConstraint(
            model_name="world",
            constraint=models.UniqueConstraint(
                condition=Q(
                    context__isnull=True,
                    instance_of__isnull=False,
                    instance_slug__isnull=False,
                ),
                fields=("instance_of", "instance_slug"),
                name="worlds_instance_template_slug_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="world",
            constraint=models.CheckConstraint(
                condition=(
                    Q(
                        context__isnull=True,
                        instance_of__isnull=False,
                        instance_slug__isnull=False,
                        instance_slug__regex=INSTANCE_SLUG_DB_PATTERN,
                    )
                    & ~Q(instance_slug__contains="--")
                    | Q(
                        instance_slug__isnull=True,
                        instance_of__isnull=True,
                    )
                    | Q(
                        instance_slug__isnull=True,
                        context__isnull=False,
                    )
                ),
                name="worlds_instance_slug_authored_template",
            ),
        ),
        migrations.RunPython(
            create_world_manifest_identity_trigger,
            reverse_code=drop_world_manifest_identity_trigger,
        ),
    ]
