from django.db import migrations, models


def migrate_questlets_to_quests(apps, schema_editor):
    QuestTemplate = apps.get_model("quests", "QuestTemplate")
    QuestTemplate.objects.filter(quest_type="questlet").update(quest_type="quest")


class Migration(migrations.Migration):

    dependencies = [
        ("quests", "0003_remove_questjournalentry_lead_and_stakes"),
    ]

    operations = [
        migrations.RunPython(
            migrate_questlets_to_quests,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="questtemplate",
            name="quest_type",
            field=models.TextField(
                choices=[
                    ("quest", "Quest"),
                    ("contract", "Contract"),
                    ("world_event", "World_event"),
                ],
                default="quest",
            ),
        ),
    ]
