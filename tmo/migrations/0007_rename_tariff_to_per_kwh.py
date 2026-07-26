from django.db import migrations, models


def divide_by_1000(apps, schema_editor):
    """Existing values were stored as ₦/MWh; convert to ₦/kWh by dividing by 1000."""
    TMOMonthlySegmentTarget = apps.get_model('tmo', 'TMOMonthlySegmentTarget')
    TMOMonthlySegmentTarget.objects.filter(
        average_tariff_per_kwh__gt=0
    ).update(average_tariff_per_kwh=models.F('average_tariff_per_kwh') / 1000)


class Migration(migrations.Migration):

    dependencies = [
        ('tmo', '0006_tmo_supply_hours_target'),
    ]

    operations = [
        migrations.RenameField(
            model_name='tmomonthlysegmenttarget',
            old_name='average_tariff_ngn_per_mwh',
            new_name='average_tariff_per_kwh',
        ),
        migrations.AlterField(
            model_name='tmomonthlysegmenttarget',
            name='average_tariff_per_kwh',
            field=models.DecimalField(
                max_digits=10, decimal_places=2, default=0,
                help_text='Average electricity price in ₦/kWh for this segment (e.g. 225 for MDI, 52 for Regions)',
            ),
        ),
        migrations.RunPython(divide_by_1000, migrations.RunPython.noop),
    ]
