from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('technical', '0009_add_dso_compliance_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='DataSyncLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('data_type', models.CharField(
                    choices=[
                        ('technical_hourly_load',    'Technical — Hourly Load'),
                        ('technical_meter_readings', 'Technical — Meter Readings'),
                        ('technical_interruptions',  'Technical — Interruptions'),
                    ],
                    db_index=True,
                    max_length=60,
                )),
                ('status', models.CharField(
                    choices=[
                        ('running', 'Running'),
                        ('success', 'Success'),
                        ('partial', 'Partial'),
                        ('error',   'Error'),
                    ],
                    default='running',
                    max_length=10,
                )),
                ('started_at',    models.DateTimeField(auto_now_add=True)),
                ('completed_at',  models.DateTimeField(blank=True, null=True)),
                ('window_start',  models.DateTimeField(blank=True, null=True)),
                ('window_end',    models.DateTimeField(blank=True, null=True)),
                ('records_fetched',  models.PositiveIntegerField(default=0)),
                ('records_created',  models.PositiveIntegerField(default=0)),
                ('records_updated',  models.PositiveIntegerField(default=0)),
                ('records_skipped',  models.PositiveIntegerField(default=0)),
                ('records_errored',  models.PositiveIntegerField(default=0)),
                ('error_message', models.TextField(blank=True)),
            ],
            options={
                'verbose_name': 'Data Sync Log',
                'verbose_name_plural': 'Data Sync Logs',
                'ordering': ['-started_at'],
            },
        ),
        migrations.AddIndex(
            model_name='datasynclog',
            index=models.Index(fields=['data_type', '-started_at'], name='tech_synclog_type_started_idx'),
        ),
    ]
