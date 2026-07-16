import hashlib
import json
from collections import Counter

from django.db import migrations


def _stable_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value):
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _plan_hash(plan, entries):
    return _digest({
        "slug": plan.slug,
        "zone_id": plan.zone_id,
        "respawn_policy": plan.respawn_policy,
        "randomization": plan.randomization,
        "conditions": plan.conditions,
        "entries": [
            {
                "slug": entry.slug,
                "order": entry.order,
                "is_active": entry.is_active,
                "source": entry.source,
                "target": entry.target,
                "count": entry.count,
                "placement": entry.placement,
                "traits": entry.traits,
                "loot": entry.loot,
                "conditions": entry.conditions,
            }
            for entry in entries
        ],
    })


def _parent_entry_slug(entry):
    target = entry.target if isinstance(entry.target, dict) else {}
    return str(target.get("entry") or target.get("parent_entry") or "").strip()


def _entry_hashes(plan, entry, parent_anchor_hash=""):
    randomization = plan.randomization if isinstance(plan.randomization, dict) else {}
    hashes = {
        "version": 1,
        "roll": _digest(randomization),
        "count": _digest({
            "randomization": randomization,
            "count": entry.count,
        }),
        "source": _digest({
            "randomization": randomization,
            "source": entry.source,
        }),
        "target": _digest({
            "randomization": randomization,
            "target": entry.target,
            "parent_anchor": parent_anchor_hash,
        }),
        "traits": _digest({
            "randomization": randomization,
            "traits": entry.traits,
        }),
        "placement": _digest({
            "placement": entry.placement,
            "parent_anchor": parent_anchor_hash,
        }),
        "loot": _digest(entry.loot),
        "conditions": _digest({
            "zone_id": plan.zone_id,
            "conditions": entry.conditions,
        }),
    }
    hashes["anchor"] = _digest({
        "source": hashes["source"],
        "target": hashes["target"],
        "placement": hashes["placement"],
    })
    hashes["materialization"] = _digest({
        key: hashes[key]
        for key in (
            "source",
            "target",
            "traits",
            "placement",
            "loot",
            "conditions",
        )
    })
    return hashes


def _entry_count_from_placements(entry, placements):
    entry_placements = [
        placement
        for placement in placements
        if placement.entry_slug == entry.slug and not placement.is_retired
    ]
    parent_slug = _parent_entry_slug(entry)
    if not parent_slug:
        return len(entry_placements)
    counts = Counter(
        (placement.parent_entry_slug, placement.parent_slot_index)
        for placement in entry_placements
        if placement.parent_slot_index is not None
    )
    if not counts:
        has_parent = any(
            placement.entry_slug == parent_slug and not placement.is_retired
            for placement in placements
        )
        if has_parent:
            return 0
        # The legacy shared RNG's child count cannot be reconstructed when
        # there were no parents. Let the first later edit establish it.
        return None
    return max(counts.values())


def _backfill_active_run_entry_states(apps, schema_editor):
    SpawnPlan = apps.get_model("builders", "SpawnPlan")
    SpawnPlanRun = apps.get_model("builders", "SpawnPlanRun")
    database = schema_editor.connection.alias
    pending = []

    plans = (
        SpawnPlan.objects.using(database)
        .all()
        .prefetch_related("entries")
        .iterator(chunk_size=100)
    )
    for plan in plans:
        entries = sorted(
            plan.entries.all(),
            key=lambda entry: (entry.order, entry.created_ts, entry.id),
        )
        current_plan_hash = _plan_hash(plan, entries)
        runs = (
            SpawnPlanRun.objects.using(database)
            .filter(
                plan_id=plan.id,
                status="active",
                spec_hash=current_plan_hash,
            )
            .prefetch_related("placements")
            .iterator(chunk_size=100)
        )
        for run in runs:
            placements = list(run.placements.all())
            hashes_by_slug = {}
            entries_state = {}
            for entry in (entry for entry in entries if entry.is_active):
                parent_slug = _parent_entry_slug(entry)
                parent_hashes = hashes_by_slug.get(parent_slug, {})
                hashes = _entry_hashes(
                    plan,
                    entry,
                    str(parent_hashes.get("anchor") or ""),
                )
                hashes_by_slug[entry.slug] = hashes
                entries_state[entry.slug] = {
                    "hashes": hashes,
                    "count": _entry_count_from_placements(entry, placements),
                }
            run.entry_states = {
                "plan_spec_hash": current_plan_hash,
                "entries": entries_state,
            }
            pending.append(run)
            if len(pending) >= 200:
                SpawnPlanRun.objects.using(database).bulk_update(
                    pending,
                    ["entry_states"],
                    batch_size=200,
                )
                pending.clear()

    if pending:
        SpawnPlanRun.objects.using(database).bulk_update(
            pending,
            ["entry_states"],
            batch_size=200,
        )


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("builders", "0241_spawn_plan_live_edit_state"),
    ]

    operations = [
        migrations.RunPython(
            _backfill_active_run_entry_states,
            _noop_reverse,
        ),
    ]
