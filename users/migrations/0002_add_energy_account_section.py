"""
users/migrations/0002_add_energy_account_section.py

Data migration: insert the Energy Account section record so that
admins can grant UserSectionAccess for the energy_account module.
"""

from django.db import migrations


def add_energy_account_section(apps, schema_editor):
    Section = apps.get_model('users', 'Section')
    Section.objects.get_or_create(
        name='energy_account',
        defaults={
            'display_name': 'Energy Account',
            'description':  (
                'Access to the Energy Account module — monthly billing returns, '
                'NBET reconciliation, feeder technical energy, TCN/MO reconciliation, '
                'meter check compliance and coupling logs.'
            ),
            'icon':      'activity',
            'is_active': True,
        },
    )


def remove_energy_account_section(apps, schema_editor):
    Section = apps.get_model('users', 'Section')
    Section.objects.filter(name='energy_account').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(
            add_energy_account_section,
            remove_energy_account_section,
        ),
    ]
