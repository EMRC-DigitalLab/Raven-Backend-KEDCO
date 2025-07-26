# analytics/management/commands/populate_overview_summary.py
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum, Count, Avg
from django.db import transaction
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta # type: ignore
from decimal import Decimal
import hashlib
import time

from analytics.models import MonthlyOverviewSummary
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered, HourlyLoad, FeederInterruption
from financial.models import Opex, SalaryPayment, NBETInvoice, MOInvoice


class Command(BaseCommand):
    help = 'Populate MonthlyOverviewSummary with calculated metrics'

    def add_arguments(self, parser):
        # Date range options
        parser.add_argument(
            '--start-year',
            type=int,
            default=2020,
            help='Start year for processing (default: 2020)'
        )
        parser.add_argument(
            '--end-year',
            type=int,
            default=datetime.now().year,
            help='End year for processing (default: current year)'
        )
        parser.add_argument(
            '--month',
            type=str,
            help='Specific month to process (format: YYYY-MM)'
        )
        parser.add_argument(
            '--current-month',
            action='store_true',
            help='Process only the current month'
        )
        parser.add_argument(
            '--last-n-months',
            type=int,
            help='Process last N months'
        )
        
        # Processing options
        parser.add_argument(
            '--force',
            action='store_true',
            help='Overwrite existing records'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be processed without making changes'
        )
        parser.add_argument(
            '--check-hash',
            action='store_true',
            help='Only recalculate if source data has changed'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=12,
            help='Number of months to process in each batch (default: 12)'
        )
        
        # Output options
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed processing information'
        )
        parser.add_argument(
            '--quiet',
            action='store_true',
            help='Minimal output'
        )

    def handle(self, *args, **options):
        self.verbosity = 2 if options['verbose'] else 1 if not options['quiet'] else 0
        
        # Determine which months to process
        months_to_process = self.get_months_to_process(options)
        
        if self.verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(f'Found {len(months_to_process)} months to process')
            )
        
        if options['dry_run']:
            self.show_dry_run(months_to_process)
            return
        
        # Process months in batches
        batch_size = options['batch_size']
        for i in range(0, len(months_to_process), batch_size):
            batch = months_to_process[i:i + batch_size]
            self.process_batch(batch, options)
        
        if self.verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS('✅ Overview summary population completed!')
            )

    def get_months_to_process(self, options):
        """Determine which months need processing based on options"""
        months = []
        
        if options['month']:
            # Process specific month
            try:
                month_date = datetime.strptime(options['month'], '%Y-%m').date().replace(day=1)
                months = [month_date]
            except ValueError:
                raise CommandError('Invalid month format. Use YYYY-MM (e.g., 2025-01)')
        
        elif options['current_month']:
            # Process current month
            today = date.today()
            months = [today.replace(day=1)]
        
        elif options['last_n_months']:
            # Process last N months
            today = date.today()
            current_month = today.replace(day=1)
            months = [
                current_month - relativedelta(months=i)
                for i in range(options['last_n_months'])
            ]
            months.reverse()  # Process oldest first
        
        else:
            # Process year range
            start_year = options['start_year']
            end_year = options['end_year']
            
            for year in range(start_year, end_year + 1):
                for month in range(1, 13):
                    month_date = date(year, month, 1)
                    # Don't process future months
                    if month_date <= date.today().replace(day=1):
                        months.append(month_date)
        
        # Filter out months that don't need processing
        if not options['force']:
            months = self.filter_months_needing_processing(months, options['check_hash'])
        
        return months

    def filter_months_needing_processing(self, months, check_hash=False):
        """Filter out months that already have up-to-date summaries"""
        months_needing_processing = []
        
        for month in months:
            try:
                existing = MonthlyOverviewSummary.objects.get(month=month)
                
                if check_hash:
                    # Check if source data has changed
                    current_hash = self.calculate_source_data_hash(month)
                    if existing.source_data_hash != current_hash:
                        months_needing_processing.append(month)
                        if self.verbosity >= 2:
                            self.stdout.write(f"📝 {month} - Data changed (hash mismatch)")
                    elif self.verbosity >= 2:
                        self.stdout.write(f"⏭️  {month} - Skipped (no changes)")
                else:
                    # For current month, always recalculate if it's older than 24 hours
                    if existing.needs_recalculation():
                        months_needing_processing.append(month)
                        if self.verbosity >= 2:
                            self.stdout.write(f"🔄 {month} - Needs refresh (> 24h old)")
                    elif self.verbosity >= 2:
                        self.stdout.write(f"⏭️  {month} - Skipped (recent)")
            
            except MonthlyOverviewSummary.DoesNotExist:
                months_needing_processing.append(month)
                if self.verbosity >= 2:
                    self.stdout.write(f"➕ {month} - New month")
        
        return months_needing_processing

    def show_dry_run(self, months):
        """Show what would be processed in dry run mode"""
        self.stdout.write(self.style.WARNING('🔍 DRY RUN MODE - No changes will be made'))
        self.stdout.write(f'Would process {len(months)} months:')
        
        for month in months:
            try:
                existing = MonthlyOverviewSummary.objects.get(month=month)
                action = "UPDATE"
                last_calc = existing.calculated_at.strftime('%Y-%m-%d %H:%M')
            except MonthlyOverviewSummary.DoesNotExist:
                action = "CREATE"
                last_calc = "Never"
            
            self.stdout.write(f"  {month} - {action} (last: {last_calc})")

    def process_batch(self, months, options):
        """Process a batch of months"""
        if self.verbosity >= 1:
            self.stdout.write(f'Processing batch: {months[0]} to {months[-1]}')
        
        for month in months:
            self.process_single_month(month, options)

    def process_single_month(self, month_date, options):
        """Process a single month"""
        start_time = time.time()
        
        if self.verbosity >= 2:
            self.stdout.write(f'Processing {month_date}...', ending='')
        
        try:
            with transaction.atomic():
                # Calculate all metrics
                metrics = self.calculate_month_metrics(month_date)
                
                # Calculate source data hash
                source_hash = self.calculate_source_data_hash(month_date)
                
                # Calculate processing duration
                duration = timedelta(seconds=time.time() - start_time)
                
                # Create or update summary
                summary, created = MonthlyOverviewSummary.objects.update_or_create(
                    month=month_date,
                    defaults={
                        **metrics,
                        'source_data_hash': source_hash,
                        'calculation_duration': duration,
                        'has_complete_data': self.check_data_completeness(metrics),
                    }
                )
                
                action = "Created" if created else "Updated"
                duration_ms = int(duration.total_seconds() * 1000)
                
                if self.verbosity >= 2:
                    self.stdout.write(f' ✅ {action} ({duration_ms}ms)')
                elif self.verbosity >= 1:
                    self.stdout.write(f'{month_date} - {action}')
        
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Failed to process {month_date}: {str(e)}')
            )
            if options['verbose']:
                import traceback
                self.stdout.write(traceback.format_exc())

    def calculate_month_metrics(self, month_date):
        """Calculate all metrics for a given month"""
        # Commercial data
        comm_data = MonthlyCommercialSummary.objects.filter(month=month_date).aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected"),
            customers_billed=Sum("customers_billed"),
            customers_responded=Sum("customers_responded"),
        )
        
        # Energy data
        energy_delivered = EnergyDelivered.objects.filter(
            date__year=month_date.year,
            date__month=month_date.month
        ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")
        
        energy_billed = MonthlyEnergyBilled.objects.filter(
            month=month_date
        ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")
        
        # Financial data
        opex_costs = Opex.objects.filter(
            date__year=month_date.year,
            date__month=month_date.month
        ).aggregate(
            total=Sum("credit") + Sum("debit")
        )["total"] or Decimal("0")
        
        salary_costs = SalaryPayment.objects.filter(
            month__year=month_date.year,
            month__month=month_date.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        nbet_costs = NBETInvoice.objects.filter(
            month__year=month_date.year,
            month__month=month_date.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        mo_costs = MOInvoice.objects.filter(
            month__year=month_date.year,
            month__month=month_date.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        # Technical metrics
        avg_hours_supply = self.calculate_avg_hours_supply(month_date)
        avg_interruption_duration, avg_turnaround_time = self.calculate_interruption_metrics(month_date)
        
        # Extract values with defaults
        revenue_billed = comm_data['revenue_billed'] or Decimal("0")
        revenue_collected = comm_data['revenue_collected'] or Decimal("0")
        customers_billed = comm_data['customers_billed'] or 0
        customers_responded = comm_data['customers_responded'] or 0
        
        # Calculate efficiency metrics
        billing_eff = (energy_billed / energy_delivered * 100) if energy_delivered > 0 else Decimal("0")
        collection_eff = (revenue_collected / revenue_billed * 100) if revenue_billed > 0 else Decimal("0")
        atc_losses = Decimal("100") - (billing_eff * collection_eff / 100) if billing_eff and collection_eff else Decimal("100")
        energy_collected = energy_delivered * (collection_eff / 100) if energy_delivered > 0 else Decimal("0")
        customer_response_rate = (customers_responded / customers_billed * 100) if customers_billed > 0 else Decimal("0")
        
        total_cost = opex_costs + salary_costs + nbet_costs + mo_costs
        
        return {
            'revenue_billed': revenue_billed,
            'revenue_collected': revenue_collected,
            'customers_billed': customers_billed,
            'customers_responded': customers_responded,
            'energy_delivered': energy_delivered,
            'energy_billed': energy_billed,
            'energy_collected': energy_collected,
            'avg_hours_supply': avg_hours_supply,
            'avg_interruption_duration': avg_interruption_duration,
            'avg_turnaround_time': avg_turnaround_time,
            'total_cost': total_cost,
            'total_opex': opex_costs,
            'total_salaries': salary_costs,
            'total_nbet': nbet_costs,
            'total_mo': mo_costs,
            'billing_efficiency': billing_eff,
            'collection_efficiency': collection_eff,
            'atc_losses': atc_losses,
            'customer_response_rate': customer_response_rate,
        }

    def calculate_avg_hours_supply(self, month_date):
        """Calculate average hours of supply for the month"""
        hourly_loads = HourlyLoad.objects.filter(
            date__year=month_date.year,
            date__month=month_date.month,
            load_mw__gt=0
        ).values('feeder', 'date').annotate(
            daily_hours=Count('hour')
        )
        
        if hourly_loads.exists():
            return hourly_loads.aggregate(avg=Avg('daily_hours'))["avg"] or Decimal("0")
        return Decimal("0")

    def calculate_interruption_metrics(self, month_date):
        """Calculate interruption and turnaround metrics"""
        interruptions = FeederInterruption.objects.filter(
            occurred_at__year=month_date.year,
            occurred_at__month=month_date.month,
            restored_at__isnull=False
        )
        
        if interruptions.exists():
            total_duration = sum(
                (interrupt.restored_at - interrupt.occurred_at).total_seconds() / 3600
                for interrupt in interruptions
            )
            avg_duration = total_duration / interruptions.count()
            return Decimal(str(round(avg_duration, 2))), Decimal(str(round(avg_duration, 2)))
        
        return Decimal("0"), Decimal("0")

    def calculate_source_data_hash(self, month_date):
        """Calculate hash of source data to detect changes"""
        # Create a string representation of key source data
        source_data = f"{month_date}"
        
        # Add counts and sums from key tables
        comm_count = MonthlyCommercialSummary.objects.filter(month=month_date).count()
        energy_count = EnergyDelivered.objects.filter(
            date__year=month_date.year, date__month=month_date.month
        ).count()
        opex_count = Opex.objects.filter(
            date__year=month_date.year, date__month=month_date.month
        ).count()
        
        source_data += f"_comm:{comm_count}_energy:{energy_count}_opex:{opex_count}"
        
        return hashlib.sha256(source_data.encode()).hexdigest()[:16]

    def check_data_completeness(self, metrics):
        """Check if we have complete data for meaningful calculations"""
        # Consider data complete if we have energy or revenue data
        return (
            metrics['energy_delivered'] > 0 or
            metrics['revenue_billed'] > 0 or
            metrics['total_cost'] > 0
        )