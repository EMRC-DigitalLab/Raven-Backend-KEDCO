from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('common', '0005_powertransformer_feeder_feeder_class_feeder_status_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='band',
            name='target_hours_per_day',
            field=models.DecimalField(
                decimal_places=1,
                default=0,
                help_text='NERC/MYTO minimum hours of supply per day for this band (A=20, B=16, C=12, D=8, E=4)',
                max_digits=4,
            ),
        ),
        # Data migration inline — set target hours based on slug
        migrations.RunSQL(
            sql="""
                UPDATE common_band SET target_hours_per_day = 20 WHERE slug = 'a';
                UPDATE common_band SET target_hours_per_day = 16 WHERE slug = 'b';
                UPDATE common_band SET target_hours_per_day = 12 WHERE slug = 'c';
                UPDATE common_band SET target_hours_per_day = 8  WHERE slug = 'd';
                UPDATE common_band SET target_hours_per_day = 4  WHERE slug = 'e';
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
