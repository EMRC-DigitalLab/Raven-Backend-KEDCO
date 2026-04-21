"""
users/migrations/0003_add_grid_lens_section.py

Data migration: insert the GridLens section record so that
admins can grant UserSectionAccess for the grid_lens module.
"""

from django.db import migrations


def add_grid_lens_section(apps, schema_editor):
    Section = apps.get_model('users', 'Section')
    Section.objects.get_or_create(
        name='grid_lens',
        defaults={
            'display_name': 'GridLens',
            'description':  (
                'Access to the GridLens loss decomposition module — energy chain analysis '
                'from TCN input to customer collection, metering gap, distribution loss, '
                'collection loss, and total ATC&C loss across the KEDCO network.'
            ),
            'icon':      'layers',
            'is_active': True,
        },
    )


def remove_grid_lens_section(apps, schema_editor):
    Section = apps.get_model('users', 'Section')
    Section.objects.filter(name='grid_lens').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_add_energy_account_section'),
    ]

    operations = [
        migrations.RunPython(
            add_grid_lens_section,
            remove_grid_lens_section,
        ),
    ]
