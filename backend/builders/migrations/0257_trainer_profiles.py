import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify


def _next_profile_slug(value, used_slugs):
    base_slug = slugify(value or "")[:120] or "trainer-profile"
    candidate = base_slug
    counter = 2
    while candidate in used_slugs:
        suffix = f"-{counter}"
        candidate = f"{base_slug[:120 - len(suffix)]}{suffix}"
        counter += 1
    used_slugs.add(candidate)
    return candidate


def _normalize_trainer_config(value):
    if not isinstance(value, dict):
        return [], "present"

    raw_abilities = value.get("abilities") or []
    if isinstance(raw_abilities, (str, int)):
        raw_abilities = [raw_abilities]
    elif not isinstance(raw_abilities, (list, tuple)):
        raw_abilities = []

    abilities = []
    seen_abilities = set()
    for raw_slug in raw_abilities:
        ability_slug = str(raw_slug or "").strip().lower()
        if ability_slug and ability_slug not in seen_abilities:
            seen_abilities.add(ability_slug)
            abilities.append(ability_slug)

    availability = str(
        value.get("availability") or "present"
    ).strip().lower()
    if availability not in {"present", "alive_and_present"}:
        availability = "present"
    return abilities, availability


def migrate_inline_mob_trainers(apps, schema_editor):
    AbilityDefinition = apps.get_model("builders", "AbilityDefinition")
    MobDefinition = apps.get_model("builders", "MobDefinition")
    TrainerProfile = apps.get_model("builders", "TrainerProfile")
    TrainerProfileAbility = apps.get_model(
        "builders",
        "TrainerProfileAbility",
    )
    database = schema_editor.connection.alias

    world_ids = (
        MobDefinition.objects.using(database)
        .order_by()
        .values_list("world_id", flat=True)
        .distinct()
    )
    for world_id in world_ids.iterator(chunk_size=500):
        mobs = list(
            MobDefinition.objects.using(database)
            .filter(world_id=world_id, trainer_profile_id__isnull=True)
            .only("id", "world_id", "slug", "name", "trainer")
            .order_by("id")
        )
        if not mobs:
            continue

        used_slugs = set(
            TrainerProfile.objects.using(database)
            .filter(world_id=world_id)
            .values_list("slug", flat=True)
        )
        specs = []
        profiles = []
        for mob in mobs:
            raw_config = mob.trainer if isinstance(mob.trainer, dict) else {}
            raw_abilities = raw_config.get("abilities") or []
            if isinstance(raw_abilities, (str, int)):
                raw_ability_count = 1
            elif isinstance(raw_abilities, (list, tuple)):
                raw_ability_count = len(raw_abilities)
            else:
                raw_ability_count = 0
            if raw_ability_count > 100:
                raise RuntimeError(
                    "Cannot migrate legacy trainer abilities for "
                    f"world {world_id}, mob {mob.id}: "
                    f"{raw_ability_count} inline entries exceeds the maximum "
                    "of 100. Reduce this mob's inline trainer abilities "
                    "before rerunning the migration."
                )
            ability_slugs, availability = _normalize_trainer_config(
                mob.trainer
            )
            if not ability_slugs:
                continue
            if len(ability_slugs) > 100:
                raise RuntimeError(
                    "Cannot migrate legacy trainer abilities for "
                    f"world {world_id}, mob {mob.id}: "
                    f"{len(ability_slugs)} abilities exceeds the maximum "
                    "of 100. Reduce this mob's inline trainer abilities "
                    "before rerunning the migration."
                )
            profile_slug = _next_profile_slug(
                f"legacy-mob-{mob.id}",
                used_slugs,
            )
            profiles.append(
                TrainerProfile(
                    world_id=world_id,
                    slug=profile_slug,
                    name=f"{mob.name or 'Unnamed Mob'} Training",
                    legacy_source_mob_id=mob.id,
                )
            )
            specs.append((mob, profile_slug, ability_slugs, availability))

        if not profiles:
            continue

        required_slugs = sorted({
            ability_slug
            for _, _, ability_slugs, _ in specs
            for ability_slug in ability_slugs
        })
        ability_ids = {}
        for offset in range(0, len(required_slugs), 500):
            ability_ids.update(
                AbilityDefinition.objects.using(database)
                .filter(
                    world_id=world_id,
                    slug__in=required_slugs[offset:offset + 500],
                )
                .values_list("slug", "id")
            )
        unresolved = [
            (mob.id, [slug for slug in ability_slugs if slug not in ability_ids])
            for mob, _, ability_slugs, _ in specs
        ]
        unresolved = [
            (mob_id, slugs)
            for mob_id, slugs in unresolved
            if slugs
        ]
        if unresolved:
            details = "; ".join(
                f"mob {mob_id}: {', '.join(slugs)}"
                for mob_id, slugs in unresolved
            )
            raise RuntimeError(
                "Cannot migrate legacy trainer abilities for "
                f"world {world_id}; unresolved ability slug(s): {details}. "
                "Fix or remove these inline trainer references before "
                "rerunning the migration."
            )

        TrainerProfile.objects.using(database).bulk_create(
            profiles,
            batch_size=500,
        )
        profile_ids = dict(
            TrainerProfile.objects.using(database)
            .filter(world_id=world_id)
            .values_list("slug", "id")
        )

        entries = []
        migrated_mobs = []
        for mob, profile_slug, ability_slugs, availability in specs:
            profile_id = profile_ids[profile_slug]
            for order, ability_slug in enumerate(ability_slugs):
                entries.append(
                    TrainerProfileAbility(
                        profile_id=profile_id,
                        ability_id=ability_ids[ability_slug],
                        order=order,
                    )
                )
            mob.trainer_profile_id = profile_id
            mob.trainer_availability = availability
            mob.trainer = {}
            migrated_mobs.append(mob)

        TrainerProfileAbility.objects.using(database).bulk_create(
            entries,
            batch_size=1000,
        )
        MobDefinition.objects.using(database).bulk_update(
            migrated_mobs,
            ["trainer_profile", "trainer_availability", "trainer"],
            batch_size=500,
        )


def restore_inline_mob_trainers(apps, schema_editor):
    MobDefinition = apps.get_model("builders", "MobDefinition")
    TrainerProfileAbility = apps.get_model(
        "builders",
        "TrainerProfileAbility",
    )
    database = schema_editor.connection.alias

    def restore_batch(mobs):
        profile_ids = {mob.trainer_profile_id for mob in mobs}
        ability_slugs_by_profile = {
            profile_id: []
            for profile_id in profile_ids
        }
        entries = (
            TrainerProfileAbility.objects.using(database)
            .filter(profile_id__in=profile_ids)
            .order_by("profile_id", "order", "id")
            .values_list("profile_id", "ability__slug")
        )
        for profile_id, ability_slug in entries.iterator(chunk_size=1000):
            ability_slugs_by_profile[profile_id].append(ability_slug)

        for mob in mobs:
            availability = str(
                mob.trainer_availability or "present"
            ).strip().lower()
            if availability not in {"present", "alive_and_present"}:
                availability = "present"
            mob.trainer = {
                "abilities": ability_slugs_by_profile[mob.trainer_profile_id],
                "availability": availability,
            }
        MobDefinition.objects.using(database).bulk_update(
            mobs,
            ["trainer"],
            batch_size=500,
        )

    batch = []
    mobs = (
        MobDefinition.objects.using(database)
        .filter(trainer_profile_id__isnull=False)
        .only("id", "trainer_profile_id", "trainer_availability")
        .order_by("id")
    )
    for mob in mobs.iterator(chunk_size=500):
        batch.append(mob)
        if len(batch) == 500:
            restore_batch(batch)
            batch = []
    if batch:
        restore_batch(batch)


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0256_delete_procession"),
    ]

    operations = [
        migrations.CreateModel(
            name="TrainerProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_ts",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "modified_ts",
                    models.DateTimeField(auto_now=True, db_index=True),
                ),
                ("slug", models.SlugField(blank=True, max_length=120)),
                ("name", models.TextField(default="Unnamed Trainer")),
                ("notes", models.TextField(blank=True, null=True)),
                (
                    "legacy_source_mob_id",
                    models.BigIntegerField(
                        blank=True,
                        db_index=True,
                        null=True,
                    ),
                ),
                (
                    "world",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="trainer_profiles",
                        to="worlds.world",
                    ),
                ),
            ],
            options={
                "ordering": ["created_ts"],
                "unique_together": {("world", "slug")},
            },
        ),
        migrations.CreateModel(
            name="TrainerProfileAbility",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_ts",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "modified_ts",
                    models.DateTimeField(auto_now=True, db_index=True),
                ),
                ("order", models.IntegerField(default=0)),
                (
                    "ability",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="trainer_profile_entries",
                        to="builders.abilitydefinition",
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ability_entries",
                        to="builders.trainerprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["order", "id"],
                "indexes": [
                    models.Index(
                        fields=["profile", "order", "id"],
                        name="builders_tr_profile_order_idx",
                    ),
                    models.Index(
                        fields=["ability", "profile"],
                        name="builders_tr_ability_prof_idx",
                    ),
                ],
                "unique_together": {("profile", "ability")},
            },
        ),
        migrations.AddField(
            model_name="trainerprofile",
            name="abilities",
            field=models.ManyToManyField(
                related_name="trainer_profiles",
                through="builders.TrainerProfileAbility",
                to="builders.abilitydefinition",
            ),
        ),
        migrations.AddField(
            model_name="mobdefinition",
            name="trainer_availability",
            field=models.TextField(blank=True, default="present"),
        ),
        migrations.AddField(
            model_name="mobdefinition",
            name="trainer_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="mob_definitions",
                to="builders.trainerprofile",
            ),
        ),
        migrations.RunPython(
            migrate_inline_mob_trainers,
            restore_inline_mob_trainers,
            atomic=True,
        ),
    ]
