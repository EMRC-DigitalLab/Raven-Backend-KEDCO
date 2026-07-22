"""
reports/management/commands/seed_tmo_templates.py

Creates the two standard TMO report templates if they don't already exist.

Usage:
    python manage.py seed_tmo_templates
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from reports.models import ReportSection, ReportTemplate

User = get_user_model()

TECHNICAL_TMO_SECTIONS = [
    ('cover_page',              'Cover Page',                       {}),
    ('table_of_contents',       'Table of Contents',                {}),
    ('infrastructure_overview', 'Infrastructure Overview',          {}),
    ('technical_metrics',       'Technical Metrics',                {}),
    ('tmo_feeder_dispatch',     'Feeder Dispatch: Target vs Actual',{}),
    ('feeder_performance_table','Feeder Performance Table',         {}),
    ('system_reliability',      'System Reliability',               {}),
    ('interruption_breakdown',  'Interruption Breakdown',           {'group_by': 'type'}),
    ('hours_of_supply_chart',   'Hours of Supply Trend',            {}),
    ('energy_delivered_chart',  'Energy Delivered Trend',           {}),
    ('gaps_improvements',       'Gaps & Improvement Areas',         {}),
]

COMMERCIAL_TMO_SECTIONS = [
    ('cover_page',                 'Cover Page',                        {}),
    ('table_of_contents',          'Table of Contents',                 {}),
    ('commercial_overview',        'Commercial Overview',               {}),
    ('tmo_billing_efficiency',     'Billing Efficiency (BE/FBE)',       {}),
    ('tmo_collection_performance', 'Collection Performance by Segment', {}),
    ('revenue_by_district',        'Revenue by District',               {}),
    ('revenue_by_feeder',          'Revenue by Feeder',                 {}),
    ('customer_type_summary',      'MDI vs MDNI Summary',               {}),
    ('gaps_improvements',          'Gaps & Improvement Areas',          {}),
]

TEMPLATES = [
    {
        'name':        'Technical TMO Report',
        'category':    'technical',
        'description': 'Standard Technical Review Meeting report — feeder dispatch, reliability, and energy performance.',
        'sections':    TECHNICAL_TMO_SECTIONS,
    },
    {
        'name':        'Commercial TMO Report',
        'category':    'commercial',
        'description': 'Standard Commercial Review Meeting report — billing efficiency, collection performance, and revenue.',
        'sections':    COMMERCIAL_TMO_SECTIONS,
    },
]


class Command(BaseCommand):
    help = 'Seed the two standard TMO report templates (Technical + Commercial)'

    def handle(self, *args, **options):
        # Use the first superuser as template owner, or first user
        user = User.objects.filter(is_superuser=True).first() or User.objects.first()
        if not user:
            self.stderr.write('No users found — create a user first.')
            return

        for tpl in TEMPLATES:
            obj, created = ReportTemplate.objects.get_or_create(
                name=tpl['name'],
                defaults={
                    'category':    tpl['category'],
                    'description': tpl['description'],
                    'created_by':  user,
                    'is_public':   True,
                    'status':      'active',
                },
            )
            if created:
                for order, (stype, title, config) in enumerate(tpl['sections']):
                    ReportSection.objects.create(
                        template=obj,
                        section_type=stype,
                        title=title,
                        config=config,
                        order=order,
                        is_enabled=True,
                    )
                self.stdout.write(self.style.SUCCESS(f'Created: {tpl["name"]}'))
            else:
                self.stdout.write(f'Already exists: {tpl["name"]} (skipped)')
