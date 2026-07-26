from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_alter_section_name'),
    ]

    operations = [
        migrations.AlterField(
            model_name='section',
            name='name',
            field=models.CharField(
                choices=[
                    ('overview',       'Overview'),
                    ('commercial',     'Commercial'),
                    ('financial',      'Financial'),
                    ('technical',      'Technical'),
                    ('hr',             'Human Resource'),
                    ('regulatory',     'Regulatory'),
                    ('energy_account', 'Energy Account'),
                    ('grid_lens',      'GridLens'),
                    ('tmo',            'TMO'),
                ],
                max_length=20,
                unique=True,
            ),
        ),
    ]
