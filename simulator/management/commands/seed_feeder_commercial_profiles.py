from datetime import date

from django.core.management.base import BaseCommand

from common.models import Feeder
from simulator.models import FeederCommercialProfile

# KEDCO benchmarks sourced from NERC Q1 2025 quarterly report.
# billing_efficiency  = 99.04% (KEDCO has strong metering coverage)
# collection_efficiency = 80.00% (improving, +6.55pp Q1 2025)
# ATC&C combined = 1 - (0.9904 × 0.80) = ~20.8% (target) / actual ~42%
KEDCO_BILLING_EFFICIENCY    = 99.04
KEDCO_COLLECTION_EFFICIENCY = 80.00
EFFECTIVE_FROM = date(2025, 1, 1)
SOURCE = 'NERC Q1 2025 KEDCO benchmark'


class Command(BaseCommand):
    help = (
        'Seeds FeederCommercialProfile for all onboarded feeders using KEDCO national benchmarks. '
        'Run this once after Phase 2 migration. Update individual profiles in admin for feeder-level overrides.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing profiles (default: skip feeders that already have a profile)',
        )

    def handle(self, *args, **options):
        overwrite = options['overwrite']
        feeders = Feeder.objects.filter(is_onboarded=True)
        created = updated = skipped = 0

        for feeder in feeders:
            existing = FeederCommercialProfile.objects.filter(feeder=feeder).first()

            if existing and not overwrite:
                skipped += 1
                continue

            if existing and overwrite:
                existing.billing_efficiency_pct    = KEDCO_BILLING_EFFICIENCY
                existing.collection_efficiency_pct = KEDCO_COLLECTION_EFFICIENCY
                existing.effective_from            = EFFECTIVE_FROM
                existing.source                    = SOURCE
                existing.is_active                 = True
                existing.save()
                updated += 1
            else:
                FeederCommercialProfile.objects.create(
                    feeder=feeder,
                    billing_efficiency_pct=KEDCO_BILLING_EFFICIENCY,
                    collection_efficiency_pct=KEDCO_COLLECTION_EFFICIENCY,
                    effective_from=EFFECTIVE_FROM,
                    source=SOURCE,
                    is_active=True,
                )
                created += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {created} created | {updated} updated | {skipped} skipped (use --overwrite to replace)'
        ))
        self.stdout.write(
            f'  Billing efficiency:    {KEDCO_BILLING_EFFICIENCY}%\n'
            f'  Collection efficiency: {KEDCO_COLLECTION_EFFICIENCY}%\n'
            f'  ATC&C combined loss:   ~{round((1 - KEDCO_BILLING_EFFICIENCY/100 * KEDCO_COLLECTION_EFFICIENCY/100)*100, 2)}%\n'
            f'  Source: {SOURCE}\n'
        )
