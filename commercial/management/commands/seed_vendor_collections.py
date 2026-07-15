"""
Seed dummy DailyCollection records for a given month using MonthlyCommercialSummary totals.
Splits each summary's revenue_collected across the available vendors on the 1st of the month.
STAGING ONLY — do not run on production.
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from commercial.models import DailyCollection, MonthlyCommercialSummary


VENDOR_WEIGHTS = {
    'Cash':          Decimal('0.30'),
    'BuyPower.ng':   Decimal('0.25'),
    'Bank':          Decimal('0.20'),
    'POS':           Decimal('0.12'),
    'Remita':        Decimal('0.07'),
    'powershop.ng':  Decimal('0.04'),
    'Banahim.net':   Decimal('0.02'),
}


class Command(BaseCommand):
    help = 'Seed dummy DailyCollection vendor data for a given month from MonthlyCommercialSummary totals'

    def add_arguments(self, parser):
        parser.add_argument('year',  type=int, help='Year  (e.g. 2024)')
        parser.add_argument('month', type=int, help='Month (e.g. 2 for February)')

    def handle(self, *args, **options):
        year  = options['year']
        month = options['month']
        seed_date = date(year, month, 1)

        summaries = MonthlyCommercialSummary.objects.filter(month=seed_date).select_related(
            'sales_rep', 'transformer'
        )

        if not summaries.exists():
            self.stdout.write(self.style.ERROR(
                f'No MonthlyCommercialSummary records found for {seed_date:%Y-%m}. Nothing to seed.'
            ))
            return

        self.stdout.write(self.style.HTTP_INFO(
            f'Found {summaries.count()} summary records for {seed_date:%B %Y}. Seeding vendor collections...'
        ))

        skipped = 0
        batch = []

        for summary in summaries:
            if not summary.revenue_collected or summary.revenue_collected <= 0:
                skipped += 1
                continue

            for vendor, weight in VENDOR_WEIGHTS.items():
                amount = (Decimal(str(summary.revenue_collected)) * weight).quantize(Decimal('0.01'))
                if amount <= 0:
                    continue

                batch.append(DailyCollection(
                    sales_rep=summary.sales_rep,
                    transformer=summary.transformer,
                    date=seed_date,
                    collection_type='Postpaid',
                    vendor_name=vendor,
                    amount=amount,
                    customers_collected=0,
                ))

        created = len(DailyCollection.objects.bulk_create(batch, ignore_conflicts=True))

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. Created {created} DailyCollection records for {seed_date:%B %Y}.'
        ))
        if skipped:
            self.stdout.write(self.style.WARNING(
                f'Skipped {skipped} summaries with zero or null revenue_collected.'
            ))
