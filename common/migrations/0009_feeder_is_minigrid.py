from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0008_band_minimum_hours_priority_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='feeder',
            name='is_minigrid',
            field=models.BooleanField(
                default=False,
                db_index=True,
                help_text='True for solar/minigrid feeders (e.g. Haske Solar) — tracked separately in TMO',
            ),
        ),
    ]
