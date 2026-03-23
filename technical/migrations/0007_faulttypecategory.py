from django.db import migrations, models


SEED_DATA = [
    # ── Load Shedding (ls) ────────────────────────────────────────────────────
    # Only the bare 'L/S' code belongs here.
    # Voltage-prefixed variants (330KV L/S, T/LS, L/S GS) go to TCN.
    {'code': 'L/S',        'label': 'Load Shedding (L/S)',                         'category': 'ls'},

    # ── Transmission / TCN (tcn) ──────────────────────────────────────────────
    {'code': 'TCN',        'label': 'TCN Fault',                                   'category': 'tcn'},
    {'code': 'tcn',        'label': 'TCN Fault (legacy lowercase)',                 'category': 'tcn'},
    {'code': '132KV E/F',  'label': '132 kV Earth Fault',                          'category': 'tcn'},
    {'code': '132KV CB/F', 'label': '132 kV Circuit Breaker Failure',              'category': 'tcn'},
    {'code': '132KV MTCE', 'label': '132 kV Maintenance (TCN)',                    'category': 'tcn'},
    {'code': '132KV L/F',  'label': '132 kV Line Fault',                           'category': 'tcn'},
    {'code': '330KV L/F',  'label': '330 kV Line Fault',                           'category': 'tcn'},
    {'code': '330KV L/S',  'label': '330 kV Line Shelving / Load Shedding',        'category': 'tcn'},
    {'code': 'T/LS',       'label': 'Thermal Load Shedding (T/LS)',                'category': 'tcn'},
    {'code': 'L/S GS',     'label': 'Load Shedding – General Supply (L/S GS)',     'category': 'tcn'},
]


def seed_fault_type_categories(apps, schema_editor):
    FaultTypeCategory = apps.get_model('technical', 'FaultTypeCategory')
    for entry in SEED_DATA:
        FaultTypeCategory.objects.get_or_create(
            code=entry['code'],
            defaults={'label': entry['label'], 'category': entry['category']},
        )


def unseed_fault_type_categories(apps, schema_editor):
    FaultTypeCategory = apps.get_model('technical', 'FaultTypeCategory')
    codes = [e['code'] for e in SEED_DATA]
    FaultTypeCategory.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('technical', '0006_alter_energydelivered_options_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='FaultTypeCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(
                    max_length=100,
                    unique=True,
                    help_text="Interruption type code exactly as it appears in feeder records (e.g. 'L/S', '132KV E/F')",
                )),
                ('label', models.CharField(
                    max_length=200,
                    blank=True,
                    help_text='Human-readable name for this fault type',
                )),
                ('category', models.CharField(
                    max_length=20,
                    choices=[
                        ('ls',    'Load Shedding'),
                        ('tcn',   'Transmission (TCN / Grid)'),
                        ('disco', 'DisCo Fault'),
                    ],
                    help_text='Category bucket — codes not listed here are classified as DisCo faults',
                )),
            ],
            options={
                'verbose_name': 'Fault Type Category',
                'verbose_name_plural': 'Fault Type Categories',
                'ordering': ['category', 'code'],
            },
        ),
        migrations.RunPython(seed_fault_type_categories, reverse_code=unseed_fault_type_categories),
    ]
