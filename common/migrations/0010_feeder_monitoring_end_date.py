from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0009_feeder_is_minigrid'),
    ]

    operations = [
        migrations.AddField(
            model_name='feeder',
            name='monitoring_end_date',
            field=models.DateField(
                null=True,
                blank=True,
                db_index=True,
                help_text=(
                    "If set, this feeder is under active monitoring until this date. "
                    "Used for newly commissioned feeders tracked in the TMO dashboard."
                ),
            ),
        ),
    ]
