import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('simulator', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PCCConfig',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('label', models.CharField(max_length=100, help_text="Descriptive label e.g. 'KEDCO PCC — NERC Order July 2025'")),
                ('total_pcc_mwh_per_hour', models.DecimalField(decimal_places=4, max_digits=10, help_text='Total DisCo PCC in MWh/h as issued by NERC (e.g. 268 for KEDCO)')),
                ('nerc_offtake_kpi_pct', models.DecimalField(decimal_places=2, default=95.0, max_digits=5, help_text='NERC minimum offtake KPI as a percentage (currently 95%)')),
                ('demand_lookback_days', models.PositiveSmallIntegerField(default=30, help_text='How many historical days to use when estimating feeder demand')),
                ('effective_from', models.DateField(help_text='Date from which this PCC config applies')),
                ('effective_to', models.DateField(blank=True, null=True, help_text='Date until which this config applies. Leave blank if currently active.')),
                ('is_active', models.BooleanField(default=True)),
                ('source', models.CharField(blank=True, max_length=255, help_text="Reference e.g. 'NERC Order No. NERC/2025/067'")),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'PCC Configuration',
                'verbose_name_plural': 'PCC Configurations',
                'ordering': ['-effective_from'],
            },
        ),
        migrations.AddField(
            model_name='simulationrun',
            name='nerc_kpi_breach',
            field=models.BooleanField(default=False, help_text='True if E_offtake is below the NERC 95% offtake KPI threshold'),
        ),
        migrations.AddField(
            model_name='simulationrun',
            name='pcc_config',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='simulation_runs',
                to='simulator.pccconfig',
                help_text='The PCC configuration used for this simulation run',
            ),
        ),
    ]
