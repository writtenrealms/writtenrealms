from django.db import migrations, models
import django.db.models.deletion


def scope_existing_offers(apps, schema_editor):
    Offer = apps.get_model('quests', 'QuestOfferState')
    Attempt = apps.get_model('quests', 'QuestInstance')
    alias = schema_editor.connection.alias
    # Old offers were shared across runs. Assign them to the latest surviving
    # attempt; offers whose runtime is already gone must not affect a new run.
    latest_attempt = Attempt.objects.using(alias).filter(
        player_id=models.OuterRef('player_id'),
        template_id=models.OuterRef('template_id'),
    ).order_by('-created_ts', '-pk')
    instance_offers = Offer.objects.using(alias).filter(
        template__world__instance_of__isnull=False,
    )
    instance_offers.update(world_id=models.Subquery(latest_attempt.values('world_id')[:1]))
    instance_offers.filter(world__isnull=True).delete()


def un_scope_offers(apps, schema_editor):
    Offer = apps.get_model('quests', 'QuestOfferState')
    offers = Offer.objects.using(schema_editor.connection.alias)
    # The previous schema can keep only one offer per character/template.
    # Retain the most recently updated state when rolling back multiple runs.
    latest = offers.filter(
        player_id=models.OuterRef('player_id'),
        template_id=models.OuterRef('template_id'),
    ).order_by('-modified_ts', '-pk').values('pk')[:1]
    offers.exclude(pk=models.Subquery(latest)).delete()
    offers.update(world_id=None)


class Migration(migrations.Migration):
    dependencies = [
        ('quests', '0008_questinstance_completed_lookup'),
        ('worlds', '0090_state_only_worldconfig_starting_eq'),
    ]

    operations = [
        migrations.AddField(
            model_name='questofferstate',
            name='world',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='quest_offer_states', to='worlds.world',
            ),
        ),
        migrations.AlterUniqueTogether(name='questofferstate', unique_together=set()),
        migrations.RunPython(scope_existing_offers, un_scope_offers),
        migrations.AddConstraint(
            model_name='questofferstate',
            constraint=models.UniqueConstraint(
                fields=('player', 'template'), condition=models.Q(world__isnull=True),
                name='quests_offer_character_unique',
            ),
        ),
        migrations.AddConstraint(
            model_name='questofferstate',
            constraint=models.UniqueConstraint(
                fields=('player', 'template', 'world'), name='quests_offer_run_unique',
            ),
        ),
    ]
