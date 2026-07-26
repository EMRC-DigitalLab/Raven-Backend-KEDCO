from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0010_feeder_monitoring_end_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='feeder',
            name='pl_segment',
            field=models.CharField(
                blank=True,
                choices=[('MDI', 'MD Industrial'), ('MDNI', 'MD Non-Industrial'), ('Regions', 'Regions')],
                db_index=True,
                help_text='P&L segment: MDI / MDNI / Regions — imported from Feeders Segmentation Excel',
                max_length=10,
                null=True,
            ),
        ),
    ]
