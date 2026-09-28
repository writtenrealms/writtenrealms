from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quests", "0007_rename_quest_room_item_description"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="questinstance",
            index=models.Index(
                fields=["player", "template"],
                condition=models.Q(status="resolved", resolution="complete"),
                name="quests_qi_completed_lookup",
            ),
        ),
    ]
