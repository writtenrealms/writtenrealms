from django.db import migrations


BATCH_SIZE = 500


def _rename_room_item_key(graph, old_name, new_name):
    if not isinstance(graph, dict):
        return False

    steps = graph.get("steps")
    if not isinstance(steps, list):
        return False

    changed = False
    for step in steps:
        if not isinstance(step, dict):
            continue
        room_items = step.get("room_items")
        if not isinstance(room_items, list):
            continue
        for room_item in room_items:
            if not isinstance(room_item, dict) or old_name not in room_item:
                continue
            # The old key supplied the active runtime behavior before this
            # migration, so it wins if an ignored extra key used the new name.
            room_item[new_name] = room_item.pop(old_name)
            changed = True
    return changed


def _rename_quest_room_item_descriptions(apps, old_name, new_name):
    QuestTemplate = apps.get_model("quests", "QuestTemplate")
    pending = []
    queryset = QuestTemplate.objects.only("id", "graph").order_by("id")

    for quest_template in queryset.iterator(chunk_size=BATCH_SIZE):
        if not _rename_room_item_key(
            quest_template.graph,
            old_name,
            new_name,
        ):
            continue
        pending.append(quest_template)
        if len(pending) >= BATCH_SIZE:
            QuestTemplate.objects.bulk_update(
                pending,
                ["graph"],
                batch_size=BATCH_SIZE,
            )
            pending = []

    if pending:
        QuestTemplate.objects.bulk_update(
            pending,
            ["graph"],
            batch_size=BATCH_SIZE,
        )


def rename_forward(apps, schema_editor):
    _rename_quest_room_item_descriptions(
        apps,
        "ground_description",
        "room_description",
    )


def rename_backward(apps, schema_editor):
    _rename_quest_room_item_descriptions(
        apps,
        "room_description",
        "ground_description",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("builders", "0252_rename_itemdefinition_ground_description"),
        ("quests", "0006_questinstance_quest_log_index"),
    ]

    operations = [
        migrations.RunPython(rename_forward, rename_backward),
    ]
