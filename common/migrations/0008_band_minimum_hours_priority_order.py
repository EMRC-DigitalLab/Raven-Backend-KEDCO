from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0007_remove_feeder_common_feed_commerc_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='band',
            name='minimum_hours',
            field=models.DecimalField(
                decimal_places=1, default=0, max_digits=4,
                help_text='NERC mandated minimum service hours per day (e.g. 20 for Band A)'
            ),
        ),
        migrations.AddField(
            model_name='band',
            name='priority_order',
            field=models.PositiveSmallIntegerField(
                default=99,
                help_text='Allocation priority: 1=highest (Band A), 5=lowest (Band E)'
            ),
        ),
    ]
