import django.db.models.deletion
import uuid
from django.db import migrations, models
from django.utils import timezone
from django.utils.text import slugify


MAX_POSITIVE_INTEGER = 2_147_483_647


def _safe_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _bounded_positive(value, default=0):
    parsed = _safe_int(value, default)
    return min(MAX_POSITIVE_INTEGER, max(0, parsed or 0))


def _safe_text(value, default=""):
    try:
        return str(value if value is not None else default).replace("\x00", "")
    except (TypeError, ValueError):
        return default


def _bounded_slug(value, default=""):
    return slugify(_safe_text(value).strip().lower())[:120] or default


def _effect_values(payload, *, world_id, encounter_id=None, target_player_id=None):
    if not isinstance(payload, dict):
        return None

    raw_remaining = _safe_int(payload.get("remaining_rounds"))
    if raw_remaining is not None and raw_remaining <= 0:
        return None

    rounds_elapsed = _bounded_positive(payload.get("rounds_elapsed"))
    raw_duration = _safe_int(payload.get("duration_rounds"))
    if raw_remaining is not None:
        remaining = min(MAX_POSITIVE_INTEGER, raw_remaining)
    elif raw_duration is not None and raw_duration > 0:
        remaining = min(MAX_POSITIVE_INTEGER, raw_duration)
    else:
        remaining = 1
    if raw_duration is not None and raw_duration > 0:
        duration = min(MAX_POSITIVE_INTEGER, raw_duration)
    else:
        duration = min(MAX_POSITIVE_INTEGER, remaining + rounds_elapsed)
    duration = max(remaining, duration)

    effect_key = _bounded_slug(payload.get("effect"), default="effect")
    tick = payload.get("tick") if isinstance(payload.get("tick"), dict) else {}
    is_character = bool(tick) or effect_key in {"dot", "hot"} or encounter_id is None
    source_ref = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    values = {
        "world_id": world_id,
        "encounter_id": encounter_id,
        "scope": "character" if is_character else "encounter",
        "effect": effect_key,
        "category": _safe_text(payload.get("category") or "neutral").strip().lower(),
        "label": _safe_text(payload.get("label") or effect_key or "Effect").strip(),
        "stack_key": _bounded_slug(payload.get("stack_key")),
        "stacking": _safe_text(payload.get("stacking") or "independent").strip().lower(),
        "remaining_rounds": remaining,
        "duration_rounds": duration,
        "rounds_elapsed": rounds_elapsed,
        "started_round": _bounded_positive(payload.get("started_round")),
        "started_round_id": _safe_text(payload.get("started_round_id") or ""),
        "primitives": payload.get("primitives") if isinstance(payload.get("primitives"), list) else [],
        "tick": tick,
        "source_snapshot": {"ref": source_ref},
        "is_hostile": effect_key == "dot",
        "next_tick_ts": timezone.now() if is_character else None,
        "target_player_id": target_player_id,
        "target_mob_id": None,
        **_actor_fields_payload(source_ref, "source"),
    }
    if target_player_id is None:
        values.update(_actor_fields_payload(payload.get("target"), "target"))
    return values


def _actor_fields_payload(ref, prefix):
    ref = ref if isinstance(ref, dict) else {}
    actor_type = _safe_text(ref.get("type") or "").strip().lower()
    actor_id = _safe_int(ref.get("id"), 0)
    actor_id = actor_id if actor_id and actor_id > 0 else None
    return {
        f"{prefix}_player_id": actor_id if actor_type == "player" else None,
        f"{prefix}_mob_id": actor_id if actor_type == "mob" else None,
    }


def _sanitize_actor_fields(
    values,
    prefix,
    *,
    world_id,
    player_worlds,
    mob_worlds,
    allowed_player_ids=None,
    allowed_mob_ids=None,
):
    player_field = f"{prefix}_player_id"
    mob_field = f"{prefix}_mob_id"
    player_id = values[player_field]
    mob_id = values[mob_field]
    if player_id and (
        (player_worlds.get(player_id) or (None,))[0] != world_id
        or (allowed_player_ids is not None and player_id not in allowed_player_ids)
    ):
        values[player_field] = None
    if mob_id and (
        (mob_worlds.get(mob_id) or (None,))[0] != world_id
        or (allowed_mob_ids is not None and mob_id not in allowed_mob_ids)
    ):
        values[mob_field] = None


def _set_source_snapshot(values, *, player_worlds, mob_worlds):
    if values["source_player_id"]:
        actor_type = "player"
        actor_id = values["source_player_id"]
        details = player_worlds.get(actor_id) or (None, "", 1, {}, 0)
    elif values["source_mob_id"]:
        actor_type = "mob"
        actor_id = values["source_mob_id"]
        details = mob_worlds.get(actor_id) or (None, "", 1, {}, 0)
    else:
        values["source_snapshot"] = {"ref": {}}
        return
    values["source_snapshot"] = {
        "ref": {"type": actor_type, "id": actor_id},
        "key": f"{actor_type}.{actor_id}",
        "name": details[1],
        "level": details[2],
        "actor_type": actor_type,
        "stats": details[3],
        "weapon_damage": details[4],
        "is_disarmed": False,
        "outgoing_damage_multiplier": 1.0,
    }


def _create_or_merge_effect(ActiveEffect, database, values):
    is_refresh_stack = (
        values["scope"] == "character"
        and values["stacking"] == "refresh"
        and bool(values["stack_key"])
    )
    if is_refresh_stack:
        target_filter = (
            {"target_player_id": values["target_player_id"]}
            if values["target_player_id"]
            else {"target_mob_id": values["target_mob_id"]}
        )
        existing = (
            ActiveEffect.objects.using(database)
            .filter(
                scope="character",
                stack_key=values["stack_key"],
                **target_filter,
            )
            .order_by("-remaining_rounds", "-id")
            .first()
        )
        if existing is not None:
            if values["remaining_rounds"] >= existing.remaining_rounds:
                ActiveEffect.objects.using(database).filter(pk=existing.pk).update(
                    **values
                )
            return
    ActiveEffect.objects.using(database).create(**values)


def migrate_active_effects(apps, schema_editor):
    ActiveEffect = apps.get_model("spawns", "ActiveEffect")
    Player = apps.get_model("spawns", "Player")
    Mob = apps.get_model("spawns", "Mob")
    CombatEncounter = apps.get_model("spawns", "CombatEncounter")

    database = schema_editor.connection.alias
    player_worlds = {
        actor_id: (world_id, name, level, {}, 0)
        for actor_id, world_id, name, level in Player.objects.using(database).values_list(
            "id", "world_id", "name", "level"
        )
    }
    mob_worlds = {}
    mob_fields = (
        "id",
        "world_id",
        "name",
        "level",
        "attack_power",
        "ability_power",
        "weapon_damage",
        "armor",
        "crit",
        "dodge",
        "resilience",
        "health_max",
        "energy_max",
        "stamina_max",
    )
    for row in Mob.objects.using(database).values_list(*mob_fields):
        actor_id, world_id, name, level, *stat_values = row
        stats = dict(zip(mob_fields[4:], stat_values))
        mob_worlds[actor_id] = (
            world_id,
            name,
            level,
            stats,
            float(stats.get("weapon_damage") or 0),
        )

    for player in Player.objects.using(database).exclude(active_effects=[]).iterator():
        payloads = player.active_effects
        if not isinstance(payloads, list):
            continue
        for payload in payloads:
            values = _effect_values(
                payload,
                world_id=player.world_id,
                target_player_id=player.id,
            )
            if values is None:
                continue
            _sanitize_actor_fields(
                values,
                "source",
                world_id=player.world_id,
                player_worlds=player_worlds,
                mob_worlds=mob_worlds,
            )
            _set_source_snapshot(
                values,
                player_worlds=player_worlds,
                mob_worlds=mob_worlds,
            )
            _create_or_merge_effect(ActiveEffect, database, values)

    encounters = (
        CombatEncounter.objects.using(database)
        .filter(status="active")
        .exclude(active_effects=[])
    )
    for encounter in encounters.iterator():
        payloads = encounter.active_effects
        if not isinstance(payloads, list):
            continue
        allowed_player_ids = {encounter.player_id}
        allowed_mob_ids = {encounter.mob_id} if encounter.mob_id else set()
        for payload in payloads:
            values = _effect_values(
                payload,
                world_id=encounter.world_id,
                encounter_id=encounter.id,
            )
            if values is None:
                continue
            _sanitize_actor_fields(
                values,
                "target",
                world_id=encounter.world_id,
                player_worlds=player_worlds,
                mob_worlds=mob_worlds,
                allowed_player_ids=allowed_player_ids,
                allowed_mob_ids=allowed_mob_ids,
            )
            if not values["target_player_id"] and not values["target_mob_id"]:
                continue
            _sanitize_actor_fields(
                values,
                "source",
                world_id=encounter.world_id,
                player_worlds=player_worlds,
                mob_worlds=mob_worlds,
                allowed_player_ids=allowed_player_ids,
                allowed_mob_ids=allowed_mob_ids,
            )
            _set_source_snapshot(
                values,
                player_worlds=player_worlds,
                mob_worlds=mob_worlds,
            )
            _create_or_merge_effect(ActiveEffect, database, values)


class Migration(migrations.Migration):

    dependencies = [
        ("spawns", "0137_remove_crafter_upgrader_fields"),
        ("worlds", "0110_alter_zone_center"),
    ]

    operations = [
        migrations.CreateModel(
            name="ActiveEffect",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("scope", models.TextField(choices=[("encounter", "Encounter"), ("character", "Character")], default="character")),
                ("effect", models.SlugField(max_length=120)),
                ("category", models.TextField(default="neutral")),
                ("label", models.TextField()),
                ("stack_key", models.SlugField(blank=True, max_length=120)),
                ("stacking", models.TextField(default="independent")),
                ("remaining_rounds", models.PositiveIntegerField(default=1)),
                ("duration_rounds", models.PositiveIntegerField(default=1)),
                ("rounds_elapsed", models.PositiveIntegerField(default=0)),
                ("started_round", models.PositiveIntegerField(default=0)),
                ("started_round_id", models.TextField(blank=True)),
                ("primitives", models.JSONField(default=list)),
                ("tick", models.JSONField(default=dict)),
                ("source_snapshot", models.JSONField(default=dict)),
                ("is_hostile", models.BooleanField(db_index=True, default=False)),
                ("next_tick_ts", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_tick_ts", models.DateTimeField(blank=True, null=True)),
                ("last_tick_token", models.TextField(blank=True)),
                ("encounter", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="character_effects", to="spawns.combatencounter")),
                ("source_mob", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sourced_active_effects", to="spawns.mob")),
                ("source_player", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sourced_active_effects", to="spawns.player")),
                ("target_mob", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="active_effect_records", to="spawns.mob")),
                ("target_player", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="active_effect_records", to="spawns.player")),
                ("world", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="active_effects", to="worlds.world")),
            ],
            options={"ordering": ["created_ts"]},
        ),
        migrations.CreateModel(
            name="GameEventOutbox",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("event_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("batch_id", models.UUIDField(db_index=True, default=uuid.uuid4, editable=False)),
                ("sequence", models.PositiveIntegerField(default=0)),
                ("event_type", models.TextField()),
                ("data", models.JSONField(default=dict)),
                ("recipients", models.JSONField(default=list)),
                ("text", models.TextField(blank=True, null=True)),
                ("group", models.TextField(blank=True, null=True)),
                ("connection_id", models.TextField(blank=True, null=True)),
                ("available_ts", models.DateTimeField(db_index=True, default=timezone.now)),
                ("claim_token", models.UUIDField(blank=True, null=True)),
                ("claimed_until", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("last_error", models.TextField(blank=True, default="")),
            ],
            options={"ordering": ["created_ts"]},
        ),
        migrations.CreateModel(
            name="EventSubscriptionReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_ts", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_ts", models.DateTimeField(auto_now=True, db_index=True)),
                ("event_id", models.UUIDField()),
                ("subscriber", models.SlugField(max_length=120)),
            ],
            options={
                "ordering": ["created_ts"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("event_id", "subscriber"),
                        name="spawns_event_receipt_unique_subscriber",
                    )
                ],
            },
        ),
        # This contract migration intentionally fails closed on reversal: the
        # legacy JSON fields cannot represent every character-scoped row that
        # may be created after this migration lands.
        migrations.RunPython(migrate_active_effects),
        migrations.RemoveField(model_name="combatencounter", name="active_effects"),
        migrations.RemoveField(model_name="player", name="active_effects"),
        migrations.AddIndex(model_name="activeeffect", index=models.Index(fields=["encounter", "remaining_rounds"], name="spawns_acti_encount_254c6f_idx")),
        migrations.AddIndex(model_name="activeeffect", index=models.Index(fields=["target_player", "remaining_rounds"], name="spawns_acti_target__3d4bcd_idx")),
        migrations.AddIndex(model_name="activeeffect", index=models.Index(fields=["target_mob", "remaining_rounds"], name="spawns_acti_target__c5e74a_idx")),
        migrations.AddIndex(model_name="activeeffect", index=models.Index(fields=["is_hostile", "next_tick_ts"], name="spawns_acti_is_host_fbb666_idx")),
        migrations.AddConstraint(
            model_name="activeeffect",
            constraint=models.CheckConstraint(
                condition=(models.Q(target_player__isnull=False, target_mob__isnull=True) | models.Q(target_player__isnull=True, target_mob__isnull=False)),
                name="spawns_effect_exactly_one_target",
            ),
        ),
        migrations.AddConstraint(
            model_name="activeeffect",
            constraint=models.CheckConstraint(
                condition=(models.Q(source_player__isnull=True) | models.Q(source_mob__isnull=True)),
                name="spawns_effect_at_most_one_source",
            ),
        ),
        migrations.AddConstraint(
            model_name="activeeffect",
            constraint=models.CheckConstraint(
                condition=models.Q(remaining_rounds__gte=1),
                name="spawns_effect_remaining_rounds_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="activeeffect",
            constraint=models.CheckConstraint(
                condition=models.Q(duration_rounds__gte=1),
                name="spawns_effect_duration_rounds_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="activeeffect",
            constraint=models.UniqueConstraint(
                fields=("target_player", "scope", "stack_key"),
                condition=(
                    models.Q(
                        scope="character",
                        stacking="refresh",
                        target_player__isnull=False,
                    )
                    & ~models.Q(stack_key="")
                ),
                name="spawns_effect_unique_player_refresh_stack",
            ),
        ),
        migrations.AddConstraint(
            model_name="activeeffect",
            constraint=models.UniqueConstraint(
                fields=("target_mob", "scope", "stack_key"),
                condition=(
                    models.Q(
                        scope="character",
                        stacking="refresh",
                        target_mob__isnull=False,
                    )
                    & ~models.Q(stack_key="")
                ),
                name="spawns_effect_unique_mob_refresh_stack",
            ),
        ),
    ]
