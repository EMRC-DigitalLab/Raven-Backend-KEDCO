from django.db import migrations, models


def migrate_feed_types_forward(apps, schema_editor):
    GoogleSheetFeed = apps.get_model('tmo', 'GoogleSheetFeed')
    GoogleSheetFeed.objects.filter(feed_type='33kv').update(feed_type='33kv_load_flow')
    GoogleSheetFeed.objects.filter(feed_type='11kv').update(feed_type='11kv_load_flow')


def migrate_feed_types_backward(apps, schema_editor):
    GoogleSheetFeed = apps.get_model('tmo', 'GoogleSheetFeed')
    GoogleSheetFeed.objects.filter(feed_type='33kv_load_flow').update(feed_type='33kv')
    GoogleSheetFeed.objects.filter(feed_type='11kv_load_flow').update(feed_type='11kv')


class Migration(migrations.Migration):

    dependencies = [
        ('tmo', '0008_add_google_sheet_feed'),
    ]

    operations = [
        migrations.AlterField(
            model_name='googlesheetfeed',
            name='feed_type',
            field=models.CharField(
                max_length=30,
                choices=[
                    ('33kv_load_flow',         '33KV Load Flow'),
                    ('33kv_energy_accounting', '33KV Energy Accounting'),
                    ('11kv_load_flow',         '11KV Load Flow'),
                    ('11kv_energy_accounting', '11KV Energy Accounting'),
                ],
                db_index=True,
            ),
        ),
        migrations.RunPython(
            migrate_feed_types_forward,
            reverse_code=migrate_feed_types_backward,
        ),
    ]
