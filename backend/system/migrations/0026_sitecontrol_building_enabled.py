from django.db import migrations, models


def copy_world_creation_policy(apps, schema_editor):
    # platform_policy.world_creation was 'all' (the default), 'none' or an
    # unimplemented 'whitelist' that behaved like 'none'.
    SiteControl = apps.get_model('system', 'SiteControl')
    for site_control in SiteControl.objects.all():
        policy = site_control.platform_policy if isinstance(site_control.platform_policy, dict) else {}
        site_control.building_enabled = policy.get('world_creation', 'all') == 'all'
        site_control.save(update_fields=['building_enabled'])


class Migration(migrations.Migration):

    dependencies = [
        ('system', '0025_sitecontrol_main_world_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitecontrol',
            name='building_enabled',
            field=models.BooleanField(default=True, help_text='Allow any signed-up user to create worlds and see the Build menu. Staff can always build, and builders keep access to worlds they already author or were added to.'),
        ),
        migrations.RunPython(copy_world_creation_policy, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='sitecontrol',
            name='platform_policy',
        ),
    ]
