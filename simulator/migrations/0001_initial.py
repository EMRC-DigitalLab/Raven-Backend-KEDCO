import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('common', '0008_band_minimum_hours_priority_order'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SimulationRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('scenario', models.CharField(
                    choices=[
                        ('actual', 'Actual'),
                        ('minimum_sbt', 'Minimum SBT'),
                        ('maximum_sbt', 'Maximum SBT'),
                        ('sbt_ration', 'SBT Ration'),
                        ('unsuppressed', 'Unsuppressed'),
                    ],
                    max_length=20
                )),
                ('simulation_date', models.DateField()),
                ('e_offtake', models.DecimalField(decimal_places=4, max_digits=14, help_text='User-supplied energy available (MWh)')),
                ('e_min', models.DecimalField(decimal_places=4, max_digits=14, help_text='NERC compliance floor (MWh)')),
                ('e_max', models.DecimalField(decimal_places=4, max_digits=14, help_text='Unsuppressed network maximum (MWh)')),
                ('e_actual', models.DecimalField(decimal_places=4, max_digits=14, help_text='Historical ground truth (MWh)')),
                ('zone', models.CharField(
                    choices=[
                        ('zone_1', 'Zone 1 — Critical Shortage'),
                        ('zone_2', 'Zone 2 — Operational'),
                        ('zone_3', 'Zone 3 — Excess Supply'),
                    ],
                    max_length=10
                )),
                ('shortage_severity', models.CharField(
                    choices=[
                        ('none', 'None'),
                        ('mild', 'Mild Shortage (75–99% of E_min)'),
                        ('moderate', 'Moderate Shortage (50–74% of E_min)'),
                        ('severe', 'Severe Shortage (25–49% of E_min)'),
                        ('critical', 'Critical / L/S (< 25% of E_min)'),
                    ],
                    default='none', max_length=10
                )),
                ('compliance_breach', models.BooleanField(default=False)),
                ('excess_supply', models.BooleanField(default=False)),
                ('band_a_greatly_downgraded', models.BooleanField(default=False)),
                ('total_allocated_mwh', models.DecimalField(decimal_places=4, default=0, max_digits=14)),
                ('surplus_mwh', models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
                ('deficit_mwh', models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
                ('deviation_from_actual', models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
                ('deviation_from_e_min', models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
                ('deviation_from_e_max', models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
                ('energised_count', models.PositiveIntegerField(default=0)),
                ('upgraded_count', models.PositiveIntegerField(default=0)),
                ('downgraded_count', models.PositiveIntegerField(default=0)),
                ('load_shed_count', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='simulation_runs',
                    to=settings.AUTH_USER_MODEL
                )),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='SimulationFeederResult',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('allocated_energy_mwh', models.DecimalField(decimal_places=4, max_digits=12)),
                ('effective_hours', models.DecimalField(decimal_places=2, max_digits=5)),
                ('status', models.CharField(
                    choices=[
                        ('energised', 'Energised'),
                        ('upgraded', 'Upgraded'),
                        ('downgraded', 'Downgraded'),
                        ('load_shed', 'Load Shed'),
                    ],
                    max_length=15
                )),
                ('forecasted_demand_mwh', models.DecimalField(decimal_places=4, max_digits=12)),
                ('band_minimum_energy_mwh', models.DecimalField(decimal_places=4, max_digits=12)),
                ('simulation', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='feeder_results',
                    to='simulator.simulationrun'
                )),
                ('feeder', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='simulation_results',
                    to='common.feeder'
                )),
                ('assigned_band', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='assigned_results', to='common.band'
                )),
                ('effective_band', models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='effective_results', to='common.band'
                )),
            ],
            options={
                'unique_together': {('simulation', 'feeder')},
            },
        ),
        migrations.AddIndex(
            model_name='simulationrun',
            index=models.Index(fields=['simulation_date'], name='simulator_s_simulat_idx'),
        ),
        migrations.AddIndex(
            model_name='simulationrun',
            index=models.Index(fields=['scenario'], name='simulator_s_scenari_idx'),
        ),
        migrations.AddIndex(
            model_name='simulationrun',
            index=models.Index(fields=['-created_at'], name='simulator_s_created_idx'),
        ),
        migrations.AddIndex(
            model_name='simulationfeederresult',
            index=models.Index(fields=['simulation', 'status'], name='simulator_f_simulat_status_idx'),
        ),
        migrations.AddIndex(
            model_name='simulationfeederresult',
            index=models.Index(fields=['feeder'], name='simulator_f_feeder_idx'),
        ),
    ]
