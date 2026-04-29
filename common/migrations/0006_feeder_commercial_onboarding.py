from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0005_powertransformer_feeder_feeder_class_feeder_status_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='feeder',
            name='commercial_is_onboarded',
            field=models.BooleanField(
                default=False,
                db_index=True,
                help_text='Whether this feeder is commercially onboarded (MDI/MDNI analytics active)',
            ),
        ),
        migrations.AddField(
            model_name='feeder',
            name='commercial_onboarded_at',
            field=models.DateField(
                blank=True,
                null=True,
                help_text='Date from which commercial analytics data is valid for this feeder',
            ),
        ),
        migrations.AddIndex(
            model_name='feeder',
            index=models.Index(fields=['commercial_is_onboarded'], name='common_feed_commerc_idx'),
        ),
    ]
