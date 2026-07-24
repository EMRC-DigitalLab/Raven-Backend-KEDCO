from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tmo', '0002_network_config_incident_tariff'),
    ]

    operations = [
        migrations.CreateModel(
            name='TMODailyAllocation',
            fields=[
                ('id',          models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date',        models.DateField(db_index=True, unique=True)),
                ('expected_mw', models.DecimalField(decimal_places=2, max_digits=10, help_text='Daily average MW allocation from TCN/NERC generation schedule')),
                ('tmo_id',      models.BigIntegerField(blank=True, db_index=True, null=True, unique=True, help_text='DataNest record ID — populated when synced; null for manual entries')),
                ('source',      models.CharField(choices=[('manual', 'Manual Entry'), ('datanest', 'DataNest Sync')], default='manual', max_length=10, help_text='How this record was created: manual admin entry or DataNest sync')),
                ('notes',       models.TextField(blank=True, help_text='Optional: source reference or remarks')),
                ('created_at',  models.DateTimeField(auto_now_add=True)),
                ('updated_at',  models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['date'],
            },
        ),
    ]
