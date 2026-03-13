from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from dateutil.relativedelta import relativedelta  # type: ignore
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from tqdm import tqdm  # type: ignore

from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from common.models import Feeder


class Command(BaseCommand):
    help = 'Estimate and populate MonthlyEnergyBilled data using revenue-based proportional allocation'

    # Energy billed data in GWh by year and month
    ENERGY_BILLED_DATA = {
        2022: [140.9, 126.0, 105.0, 109.0, 96.0, 86.0, 101.0, 116.0, 104.0, 112.0, 125.8, 124.1],
        2023: [118.7, 114.4, 134.9, 103.7, 104.4, 95.9, 95.6, 97.5, 99.2, 109.0, 124.6, 128.3],
        2024: [116.0, 102.8, 111.1, 103.4, 122.0, 106.2, 136.9, 137.8, 141.2, 85.0, 80.3, 109.2],
        2025: [99.9, 104.0, 140.8, 134.5],
    }

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be processed without making changes',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Overwrite existing records',
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        self.force = options.get('force', False)
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be made"))

        try:
            self.process_all_data()
        except Exception as e:
            raise CommandError(f'Error during processing: {str(e)}')

    def process_all_data(self):
        """Process all available energy billed data using revenue-based allocation"""
        self.stdout.write(self.style.HTTP_INFO("Processing all available energy billed data using revenue-based allocation..."))
        
        total_processed = 0
        total_created = 0
        total_updated = 0
        total_skipped = 0

        for year, monthly_data in self.ENERGY_BILLED_DATA.items():
            self.stdout.write(f"\nProcessing year {year}...")
            
            year_stats = self.process_year(year, monthly_data)
            total_processed += year_stats['processed']
            total_created += year_stats['created']
            total_updated += year_stats['updated']
            total_skipped += year_stats['skipped']

        self.print_summary(total_processed, total_created, total_updated, total_skipped)

    def process_year(self, year, monthly_data):
        """Process all months in a specific year"""
        total_processed = 0
        total_created = 0
        total_updated = 0
        total_skipped = 0

        for month_index, energy_gwh in enumerate(monthly_data, 1):
            month_stats = self.process_specific_month(year, month_index, energy_gwh)
            total_processed += month_stats['processed']
            total_created += month_stats['created']
            total_updated += month_stats['updated']
            total_skipped += month_stats['skipped']

        return {
            'processed': total_processed,
            'created': total_created,
            'updated': total_updated,
            'skipped': total_skipped
        }

    def process_specific_month(self, year, month, energy_gwh):
        """Process a specific year and month using revenue-based allocation"""
        # Convert GWh to MWh
        disco_total_energy_billed_mwh = Decimal(str(energy_gwh * 1000))
        
        # Create the month date (first day of month)
        month_date = date(year, month, 1)

        self.stdout.write(
            f"Processing {month_date.strftime('%B %Y')}: "
            f"{energy_gwh} GWh ({disco_total_energy_billed_mwh} MWh)"
        )

        # Get total revenue billed across all transformers for this month
        total_revenue_billed = MonthlyCommercialSummary.objects.filter(
            month=month_date
        ).aggregate(total=Sum('revenue_billed'))['total'] or Decimal('0')

        if total_revenue_billed == 0:
            self.stdout.write(
                self.style.WARNING(
                    f"No revenue billed data found for {month_date.strftime('%B %Y')}. "
                    f"Skipping this month."
                )
            )
            return {'processed': 0, 'created': 0, 'updated': 0, 'skipped': 1}

        self.stdout.write(f"Total revenue billed: ₦{total_revenue_billed:,}")

        # Get all feeders that have transformers with revenue data for this month
        feeders_with_revenue = Feeder.objects.filter(
            transformers__monthlycommercialsummary__month=month_date
        ).distinct()

        if not feeders_with_revenue.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"No feeders with revenue data found for {month_date.strftime('%B %Y')}"
                )
            )
            return {'processed': 0, 'created': 0, 'updated': 0, 'skipped': 1}

        processed = 0
        created = 0
        updated = 0
        skipped = 0

        progress_desc = f"Processing feeders for {month_date.strftime('%b %Y')}"
        
        for feeder in tqdm(feeders_with_revenue, desc=progress_desc, unit="feeder"):
            # Calculate total revenue billed for all transformers under this feeder
            feeder_revenue_billed = MonthlyCommercialSummary.objects.filter(
                transformer__feeder=feeder,
                month=month_date
            ).aggregate(total=Sum('revenue_billed'))['total'] or Decimal('0')

            if feeder_revenue_billed == 0:
                skipped += 1
                continue

            # Calculate revenue-based proportional allocation
            # Feeder_Billed_Energy = (Feeder_Revenue / Total_Revenue) × DisCo_Total_Billed_Energy
            revenue_proportion = feeder_revenue_billed / total_revenue_billed
            feeder_billed_energy = (revenue_proportion * disco_total_energy_billed_mwh).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )

            if not self.dry_run:
                # Create or update the record
                monthly_billed, record_created = MonthlyEnergyBilled.objects.update_or_create(
                    feeder=feeder,
                    month=month_date,
                    defaults={'energy_mwh': feeder_billed_energy}
                )

                if record_created:
                    created += 1
                else:
                    if self.force:
                        updated += 1
                    else:
                        # Record exists and force not specified
                        skipped += 1
                        continue
            else:
                # Dry run - check if record exists
                exists = MonthlyEnergyBilled.objects.filter(
                    feeder=feeder,
                    month=month_date
                ).exists()
                
                if exists and not self.force:
                    skipped += 1
                elif exists:
                    updated += 1
                else:
                    created += 1

            processed += 1

        return {
            'processed': processed,
            'created': created,
            'updated': updated,
            'skipped': skipped
        }

    def print_summary(self, processed, created, updated, skipped):
        """Print processing summary"""
        self.stdout.write(f"\n{'='*60}")
        if self.dry_run:
            self.stdout.write(self.style.SUCCESS("DRY RUN SUMMARY"))
        else:
            self.stdout.write(self.style.SUCCESS("PROCESSING COMPLETE"))
        self.stdout.write(f"{'='*60}")
        self.stdout.write(self.style.SUCCESS(f"Records processed: {processed}"))
        self.stdout.write(self.style.SUCCESS(f"Records created: {created}"))
        if updated > 0:
            self.stdout.write(self.style.SUCCESS(f"Records updated: {updated}"))
        if skipped > 0:
            self.stdout.write(self.style.WARNING(f"Records skipped: {skipped}"))
        self.stdout.write(f"{'='*60}")

        if self.dry_run:
            self.stdout.write(
                self.style.HTTP_INFO(
                    "To execute these changes, run the command without --dry-run"
                )
            )
        elif skipped > 0 and not self.force:
            self.stdout.write(
                self.style.HTTP_INFO(
                    "To overwrite existing records, use the --force flag"
                )
            )