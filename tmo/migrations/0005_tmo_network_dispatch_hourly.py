from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tmo', '0004_tmo_network_dispatch'),
    ]

    operations = [
        migrations.CreateModel(
            name='TMONetworkDispatchHourly',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(db_index=True)),
                ('hour', models.PositiveSmallIntegerField(help_text='1=01:00, 2=02:00, … 24=24:00')),
                ('kedco_allocation_mw', models.DecimalField(decimal_places=4, max_digits=10, null=True)),
                ('disco_offtake_mw', models.DecimalField(decimal_places=4, max_digits=10, null=True)),
                ('variance_mw', models.DecimalField(decimal_places=4, max_digits=10, null=True)),
                ('available_generation_mw', models.DecimalField(decimal_places=4, max_digits=12, null=True)),
                ('dispatch', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='hourly_readings',
                    to='tmo.tmonetworkdispatch',
                )),
            ],
            options={
                'ordering': ['date', 'hour'],
                'unique_together': {('date', 'hour')},
            },
        ),
    ]
