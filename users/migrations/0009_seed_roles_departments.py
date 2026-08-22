from django.db import migrations

# is_system=True for these three: referenced by literal string in permission
# checks across technical/tmo/analytics/energy_account/grid_view/reports and
# in NotificationService.notify_role() call sites — deleting them would
# silently break access checks elsewhere. staff/viewer aren't depended on
# anywhere outside this app, so they stay fully editable/deletable.
ROLES = [
    ('super_admin', 'Super Admin', True),
    ('admin', 'Admin', True),
    ('manager', 'Manager', True),
    ('staff', 'Staff', False),
    ('viewer', 'Viewer', False),
]

# Union of what's actually assigned to real users today (Management,
# Commercial, TMO, Technical) and what the frontend's pre-existing hardcoded
# dropdown already offered (Financial, Human Resources, Regulatory,
# IT Support, Operations) — nothing currently selectable disappears.
DEPARTMENTS = [
    'Commercial', 'Financial', 'Technical', 'Human Resources', 'Regulatory',
    'IT Support', 'Management', 'Operations', 'TMO',
]


def seed(apps, schema_editor):
    Role = apps.get_model('users', 'Role')
    Department = apps.get_model('users', 'Department')

    for name, display_name, is_system in ROLES:
        Role.objects.get_or_create(name=name, defaults={'display_name': display_name, 'is_system': is_system})

    for name in DEPARTMENTS:
        Department.objects.get_or_create(name=name)


def unseed(apps, schema_editor):
    Role = apps.get_model('users', 'Role')
    Department = apps.get_model('users', 'Department')
    Role.objects.filter(name__in=[r[0] for r in ROLES]).delete()
    Department.objects.filter(name__in=DEPARTMENTS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_department_role'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
