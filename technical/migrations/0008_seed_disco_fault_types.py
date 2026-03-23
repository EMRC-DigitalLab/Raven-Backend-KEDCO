from django.db import migrations

# All known DisCo fault codes — sourced from FAULT_CODE_MAP in import_hourly_faults.py
# and FeederInterruption.INTERRUPTION_TYPES in models.py.
# These are local distribution faults under the DisCo's control.
DISCO_SEED = [
    {'code': 'E/F',       'label': 'Earth Fault'},
    {'code': 'O/C',       'label': 'Overcurrent'},
    {'code': 'O/C & E/F', 'label': 'Overcurrent and Earth Fault'},
    {'code': 'OC & E/F',  'label': 'Open Circuit and Earth Fault'},
    {'code': 'NO RI',     'label': 'No RI'},
    {'code': 'N/A',       'label': 'Not Specified'},
    {'code': 'O/S',       'label': 'Overload / Overcurrent (O/S)'},
    {'code': 'T/F',       'label': 'Transformer Fault'},
    {'code': 'B/F',       'label': 'Bus / Breakdown Fault'},
    {'code': 'O/N',       'label': 'Overheating / Overtemp'},
    {'code': 'O/E',       'label': 'Open Earth'},
    {'code': 'P/O',       'label': 'Phase Open'},
    {'code': 'O/F',       'label': 'Over Frequency'},
    {'code': 'P/M',       'label': 'Phase Missing / Phase Metering'},
    {'code': 'O',         'label': 'Other / Operational Fault'},
    {'code': 'T/S',       'label': 'Trip / Surge Fault'},
    {'code': 'MTNC',      'label': 'Maintenance (MTNC)'},
    {'code': 'MTCE',      'label': 'Maintenance (MTCE)'},
    {'code': 'EM/D',      'label': 'Emergency / Device Fault'},
    {'code': 'OFF',       'label': 'Switch Off / Feeder Off'},
    {'code': 'S/C',       'label': 'Short Circuit'},
    {'code': 'D/C',       'label': 'Double Circuit / Direct Current'},
    {'code': 'IN O/C',    'label': 'Incoming Overcurrent / Infeeder OC'},
    {'code': 'LIM',       'label': 'Lightning Impulse / Limit'},
    {'code': 'fault',     'label': 'Fault (generic)'},
    {'code': 'permit',    'label': 'Permit'},
]


def seed_disco(apps, schema_editor):
    FaultTypeCategory = apps.get_model('technical', 'FaultTypeCategory')
    for entry in DISCO_SEED:
        FaultTypeCategory.objects.get_or_create(
            code=entry['code'],
            defaults={'label': entry['label'], 'category': 'disco'},
        )


def unseed_disco(apps, schema_editor):
    FaultTypeCategory = apps.get_model('technical', 'FaultTypeCategory')
    FaultTypeCategory.objects.filter(
        code__in=[e['code'] for e in DISCO_SEED]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('technical', '0007_faulttypecategory'),
    ]

    operations = [
        migrations.RunPython(seed_disco, reverse_code=unseed_disco),
    ]
