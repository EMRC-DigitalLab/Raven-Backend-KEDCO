# analytics/management/commands/populate_overview_summary.py
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum, Count, Avg, Q, F
from django.db import transaction, connection
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta # type: ignore
from decimal import Decimal
from django.utils import timezone
import hashlib
import time

from analytics.models import MonthlyOverviewSummary
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered, HourlyLoad, FeederInterruption
from financial.models import Opex, SalaryPayment, NBETInvoice, MOInvoice


class Command(BaseCommand):
    help = 'Populate MonthlyOverviewSummary with calculated metrics (OPTIMIZED)'

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
            default=100,
            help='Number of months to process in bulk operation (default: 100)'
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
        parser.add_argument(
            '--use-bulk',
            action='store_true',
            help='Use bulk operations (faster but less granular error handling)'
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
        
        # Process months
        if options['use_bulk'] and len(months_to_process) > 1:
            self.process_bulk(months_to_process, options)
        else:
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
        """Filter out months that already have up-to-date summaries - OPTIMIZED"""
        if not months:
            return []
        
        # Get all existing summaries in ONE query
        existing_summaries = {
            summary.month: summary
            for summary in MonthlyOverviewSummary.objects.filter(month__in=months)
        }
        
        months_needing_processing = []
        
        for month in months:
            existing = existing_summaries.get(month)
            
            if existing is None:
                months_needing_processing.append(month)
                if self.verbosity >= 2:
                    self.stdout.write(f"➕ {month} - New month")
                continue
            
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
        
        return months_needing_processing

    def show_dry_run(self, months):
        """Show what would be processed in dry run mode"""
        self.stdout.write(self.style.WARNING('🔍 DRY RUN MODE - No changes will be made'))
        self.stdout.write(f'Would process {len(months)} months:')
        
        # Fetch all existing in one query
        existing = {
            s.month: s 
            for s in MonthlyOverviewSummary.objects.filter(month__in=months)
        }
        
        for month in months:
            if month in existing:
                action = "UPDATE"
                last_calc = existing[month].calculated_at.strftime('%Y-%m-%d %H:%M')
            else:
                action = "CREATE"
                last_calc = "Never"
            
            self.stdout.write(f"  {month} - {action} (last: {last_calc})")

    def process_bulk(self, months, options):
        """Process all months using bulk operations for maximum speed"""
        if self.verbosity >= 1:
            self.stdout.write(f'Using BULK processing for {len(months)} months...')
        
        start_time = time.time()
        
        # Pre-fetch all data for ALL months in efficient queries
        all_metrics = self.calculate_all_months_metrics_bulk(months)
        
        # Prepare bulk operations
        to_create = []
        to_update = []
        
        existing = {
            s.month: s
            for s in MonthlyOverviewSummary.objects.filter(month__in=months)
        }
        
        for month in months:
            metrics = all_metrics.get(month, {})
            source_hash = self.calculate_source_data_hash(month)
            has_complete = self.check_data_completeness(metrics)
            
            if month in existing:
                # Update existing
                obj = existing[month]
                for key, value in metrics.items():
                    setattr(obj, key, value)
                obj.source_data_hash = source_hash
                obj.has_complete_data = has_complete
                to_update.append(obj)
            else:
                # Create new
                to_create.append(MonthlyOverviewSummary(
                    month=month,
                    source_data_hash=source_hash,
                    has_complete_data=has_complete,
                    **metrics
                ))
        
        # Execute bulk operations
        with transaction.atomic():
            if to_create:
                MonthlyOverviewSummary.objects.bulk_create(to_create, batch_size=100)
                if self.verbosity >= 1:
                    self.stdout.write(f'✅ Created {len(to_create)} records')
            
            if to_update:
                MonthlyOverviewSummary.objects.bulk_update(
                    to_update,
                    [
                        'revenue_billed', 'revenue_collected', 'customers_billed',
                        'customers_responded', 'energy_delivered', 'energy_billed',
                        'energy_collected', 'avg_hours_supply', 'avg_interruption_duration',
                        'avg_turnaround_time', 'total_cost', 'total_opex', 'total_salaries',
                        'total_nbet', 'total_mo', 'billing_efficiency', 'collection_efficiency',
                        'atc_losses', 'customer_response_rate', 'source_data_hash',
                        'has_complete_data'
                    ],
                    batch_size=100
                )
                if self.verbosity >= 1:
                    self.stdout.write(f'✅ Updated {len(to_update)} records')
        
        duration = time.time() - start_time
        if self.verbosity >= 1:
            self.stdout.write(f'⚡ Bulk processing completed in {duration:.2f}s ({len(months)/duration:.1f} months/sec)')

    def calculate_all_months_metrics_bulk(self, months):
        """Calculate metrics for ALL months in optimized bulk queries"""
        if not months:
            return {}
        
        # Create year-month tuples for filtering
        year_months = [(m.year, m.month) for m in months]
        
        # Build Q objects for efficient filtering
        date_q = Q()
        for year, month in year_months:
            date_q |= Q(date__year=year, date__month=month)
        
        month_q = Q()
        for month in months:
            month_q |= Q(month=month)
        
        # COMMERCIAL DATA - Single query for all months
        comm_data = {}
        for row in MonthlyCommercialSummary.objects.filter(month__in=months).values('month').annotate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected"),
            customers_billed=Sum("customers_billed"),
            customers_responded=Sum("customers_responded"),
        ):
            comm_data[row['month']] = row
        
        # ENERGY DELIVERED - Single query grouped by month
        energy_delivered = {}
        for row in EnergyDelivered.objects.filter(date_q).values(
            year=F('date__year'), month=F('date__month')
        ).annotate(total=Sum("energy_mwh")):
            month_key = date(row['year'], row['month'], 1)
            energy_delivered[month_key] = row['total'] or Decimal("0")
        
        # ENERGY BILLED - Single query
        energy_billed = {}
        for row in MonthlyEnergyBilled.objects.filter(month__in=months).values('month').annotate(
            total=Sum("energy_mwh")
        ):
            energy_billed[row['month']] = row['total'] or Decimal("0")
        
        # FINANCIAL DATA - Single queries for each type
        opex_costs = {}
        for row in Opex.objects.filter(date_q).values(
            year=F('date__year'), month=F('date__month')
        ).annotate(total=Sum(F("credit") + F("debit"))):
            month_key = date(row['year'], row['month'], 1)
            opex_costs[month_key] = row['total'] or Decimal("0")
        
        salary_costs = {}
        for row in SalaryPayment.objects.filter(
            Q(month__in=months)
        ).values('month').annotate(total=Sum("amount")):
            salary_costs[row['month']] = row['total'] or Decimal("0")
        
        nbet_costs = {}
        for row in NBETInvoice.objects.filter(
            Q(month__in=months)
        ).values('month').annotate(total=Sum("amount")):
            nbet_costs[row['month']] = row['total'] or Decimal("0")
        
        mo_costs = {}
        for row in MOInvoice.objects.filter(
            Q(month__in=months)
        ).values('month').annotate(total=Sum("amount")):
            mo_costs[row['month']] = row['total'] or Decimal("0")
        
        # TECHNICAL METRICS - Bulk calculation
        hours_supply = self.calculate_avg_hours_supply_bulk(months)
        interruption_metrics = self.calculate_interruption_metrics_bulk(months)
        
        # Assemble metrics for each month
        all_metrics = {}
        
        for month in months:
            comm = comm_data.get(month, {})
            energy_del = energy_delivered.get(month, Decimal("0"))
            energy_bil = energy_billed.get(month, Decimal("0"))
            opex = opex_costs.get(month, Decimal("0"))
            salary = salary_costs.get(month, Decimal("0"))
            nbet = nbet_costs.get(month, Decimal("0"))
            mo = mo_costs.get(month, Decimal("0"))
            
            revenue_billed = comm.get('revenue_billed') or Decimal("0")
            revenue_collected = comm.get('revenue_collected') or Decimal("0")
            customers_billed = comm.get('customers_billed') or 0
            customers_responded = comm.get('customers_responded') or 0
            
            # Calculate efficiency metrics
            billing_eff = (energy_bil / energy_del * 100) if energy_del > 0 else Decimal("0")
            collection_eff = (revenue_collected / revenue_billed * 100) if revenue_billed > 0 else Decimal("0")
            atc_losses = Decimal("100") - (billing_eff * collection_eff / 100) if billing_eff and collection_eff else Decimal("100")
            energy_collected = energy_bil * (collection_eff / 100) if energy_del > 0 else Decimal("0")
            customer_response_rate = (customers_responded / customers_billed * 100) if customers_billed > 0 else Decimal("0")
            
            total_cost = opex + salary + nbet + mo
            
            avg_hours_sup, avg_int_dur, avg_turn = interruption_metrics.get(month, (Decimal("0"), Decimal("0"), Decimal("0")))
            
            all_metrics[month] = {
                'revenue_billed': revenue_billed,
                'revenue_collected': revenue_collected,
                'customers_billed': customers_billed,
                'customers_responded': customers_responded,
                'energy_delivered': energy_del,
                'energy_billed': energy_bil,
                'energy_collected': energy_collected,
                'avg_hours_supply': hours_supply.get(month, Decimal("0")),
                'avg_interruption_duration': avg_int_dur,
                'avg_turnaround_time': avg_turn,
                'total_cost': total_cost,
                'total_opex': opex,
                'total_salaries': salary,
                'total_nbet': nbet,
                'total_mo': mo,
                'billing_efficiency': billing_eff,
                'collection_efficiency': collection_eff,
                'atc_losses': atc_losses,
                'customer_response_rate': customer_response_rate,
            }
        
        return all_metrics

    def calculate_avg_hours_supply_bulk(self, months):
        """Calculate average hours of supply for multiple months - OPTIMIZED"""
        results = {}
        
        # Build Q object for all months
        date_q = Q()
        for month in months:
            date_q |= Q(date__year=month.year, date__month=month.month)
        
        # Single query for all months
        for row in HourlyLoad.objects.filter(
            date_q, load_mw__gt=0
        ).values(
            year=F('date__year'), 
            month=F('date__month'),
            feeder_id=F('feeder'),
            date_val=F('date')
        ).annotate(
            daily_hours=Count('hour')
        ).values(
            'year', 'month'
        ).annotate(
            avg_hours=Avg('daily_hours')
        ):
            month_key = date(row['year'], row['month'], 1)
            results[month_key] = Decimal(str(row['avg_hours'] or 0))
        
        return results

    def calculate_interruption_metrics_bulk(self, months):
        """Calculate interruption metrics for multiple months - OPTIMIZED"""
        from technical.models import calculate_interruption_metrics
        import calendar
        
        results = {}
        
        # Build Q object for all months
        occurred_q = Q()
        for month in months:
            occurred_q |= Q(occurred_at__year=month.year, occurred_at__month=month.month)
        
        # Get all interruptions in one query
        all_interruptions = list(FeederInterruption.objects.filter(occurred_q).select_related('feeder'))
        
        # Group by month
        interruptions_by_month = {}
        for interruption in all_interruptions:
            month_key = date(interruption.occurred_at.year, interruption.occurred_at.month, 1)
            if month_key not in interruptions_by_month:
                interruptions_by_month[month_key] = []
            interruptions_by_month[month_key].append(interruption)
        
        # Calculate metrics for each month
        for month in months:
            month_interruptions = interruptions_by_month.get(month, [])
            
            if not month_interruptions:
                results[month] = (Decimal("0"), Decimal("0"), Decimal("0"))
                continue
            
            # Calculate end of month
            days_in_month = calendar.monthrange(month.year, month.month)[1]
            end_of_month = datetime(month.year, month.month, days_in_month, 23, 59, 59)
            end_of_month = timezone.make_aware(end_of_month) if timezone.is_naive(end_of_month) else end_of_month
            
            # Convert list to queryset-like for utility function
            from django.db.models.query import QuerySet
            
            # Calculate metrics directly without creating queryset
            total_duration = 0
            count = len(month_interruptions)
            
            for interruption in month_interruptions:
                duration = interruption.get_duration_hours_at_time(end_of_month)
                total_duration += duration
            
            avg_duration = Decimal(str(total_duration / count)) if count > 0 else Decimal("0")
            
            results[month] = (Decimal("0"), avg_duration, avg_duration)
        
        return results

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
        """Calculate all metrics for a given month (single month version)"""
        # Use bulk method for single month
        return self.calculate_all_months_metrics_bulk([month_date]).get(month_date, {})

    def calculate_source_data_hash(self, month_date):
        """Calculate hash of source data to detect changes - OPTIMIZED"""
        # Use efficient count queries
        with connection.cursor() as cursor:
            source_data = f"{month_date}"
            
            # Single query with multiple counts
            cursor.execute("""
                SELECT 
                    (SELECT COUNT(*) FROM commercial_monthlycommercialsummary WHERE month = %s),
                    (SELECT COUNT(*) FROM technical_energydelivered WHERE EXTRACT(YEAR FROM date) = %s AND EXTRACT(MONTH FROM date) = %s),
                    (SELECT COUNT(*) FROM financial_opex WHERE EXTRACT(YEAR FROM date) = %s AND EXTRACT(MONTH FROM date) = %s)
            """, [month_date, month_date.year, month_date.month, month_date.year, month_date.month])
            
            comm_count, energy_count, opex_count = cursor.fetchone()
            
            source_data += f"_comm:{comm_count}_energy:{energy_count}_opex:{opex_count}"
            
            return hashlib.sha256(source_data.encode()).hexdigest()[:16]

    def check_data_completeness(self, metrics):
        """Check if we have complete data for meaningful calculations"""
        # Consider data complete if we have energy or revenue data
        return (
            metrics.get('energy_delivered', 0) > 0 or
            metrics.get('revenue_billed', 0) > 0 or
            metrics.get('total_cost', 0) > 0
        )