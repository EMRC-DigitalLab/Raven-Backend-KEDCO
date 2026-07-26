from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tmo', '0005_tmo_network_dispatch_hourly'),
    ]

    operations = [
        migrations.CreateModel(
            name='TMOSupplyHoursTarget',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('segment', models.CharField(
                    choices=[
                        ('MDI', 'MDI'),
                        ('Non-MDI Band A', 'Non-MDI Band A'),
                        ('Non-MDI, Non-Band A', 'Non-MDI, Non-Band A'),
                    ],
                    db_index=True, max_length=30,
                )),
                ('year',  models.PositiveSmallIntegerField()),
                ('month', models.PositiveSmallIntegerField()),
                ('target_hours', models.DecimalField(
                    decimal_places=2, max_digits=5,
                    help_text='Daily hours-of-supply target for feeders in this segment (e.g. 20.00)',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-year', '-month', 'segment'],
                'unique_together': {('segment', 'year', 'month')},
            },
        ),
    ]
