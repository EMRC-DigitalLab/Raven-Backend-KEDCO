from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0009_feeder_is_minigrid'),
        ('tmo', '0001_initial'),
    ]

    operations = [
        # Add average tariff to TMOMonthlySegmentTarget
        migrations.AddField(
            model_name='tmomonthlysegmenttarget',
            name='average_tariff_ngn_per_mwh',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=12,
                help_text='Average tariff in ₦/MWh for this segment (used for GCR billing value calc)',
            ),
        ),
        # Add Regions to segment choices (no DB change needed — CharField)

        # Create TMONetworkConfig
        migrations.CreateModel(
            name='TMONetworkConfig',
            fields=[
                ('id',                        models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year',                      models.PositiveSmallIntegerField()),
                ('month',                     models.PositiveSmallIntegerField()),
                ('target_md_share_pct',       models.DecimalField(decimal_places=2, default=65.0, max_digits=5, help_text='Target share of total energy for MD (MDI+MDNI) feeders, e.g. 65.0')),
                ('monthly_energy_target_gwh', models.DecimalField(decimal_places=4, default=0, max_digits=12, help_text='Monthly total energy target in GWh (shown on daily forecast chart)')),
                ('created_at',                models.DateTimeField(auto_now_add=True)),
                ('updated_at',                models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-year', '-month'],
                'unique_together': {('year', 'month')},
            },
        ),

        # Create TMOIncident
        migrations.CreateModel(
            name='TMOIncident',
            fields=[
                ('id',                  models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('feeder',              models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tmo_incidents', to='common.feeder')),
                ('coordinate',         models.CharField(blank=True, max_length=100, help_text='e.g. JIGAWA, KATSINA')),
                ('region',             models.CharField(blank=True, max_length=150, help_text='e.g. JIGAWA SOUTH')),
                ('nature_of_fault',    models.TextField()),
                ('status',             models.CharField(choices=[('rectified', 'Rectified'), ('lingering', 'Lingering')], db_index=True, default='lingering', max_length=20)),
                ('financial_loss_ngn', models.DecimalField(decimal_places=2, default=0, max_digits=16)),
                ('incident_date',      models.DateField(db_index=True)),
                ('rectified_date',     models.DateField(blank=True, null=True)),
                ('created_at',         models.DateTimeField(auto_now_add=True)),
                ('updated_at',         models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-incident_date', 'status'],
            },
        ),
    ]
