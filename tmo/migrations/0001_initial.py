from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='TMOMonthlySegmentTarget',
            fields=[
                ('id',                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('segment',               models.CharField(choices=[('MDI', 'MD Industrial'), ('MDNI', 'MD Non-Industrial')], db_index=True, max_length=10)),
                ('year',                  models.PositiveSmallIntegerField()),
                ('month',                 models.PositiveSmallIntegerField()),
                ('target_energy_mwh',     models.DecimalField(decimal_places=4, default=0, max_digits=14)),
                ('target_revenue_ngn',    models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('target_collection_ngn', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('created_at',            models.DateTimeField(auto_now_add=True)),
                ('updated_at',            models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-year', '-month', 'segment'],
                'unique_together': {('segment', 'year', 'month')},
            },
        ),
    ]
