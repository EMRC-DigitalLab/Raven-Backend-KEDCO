# analytics/management/commands/populate_daily_technical_summary.py
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum, Count, Avg
from django.db import transaction
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from decimal import Decimal
import hashlib
import time

from analytics.models import DailyTechnicalSummary
from analytics.utils.technical_calculations import TechnicalCalculator
from common.models import State, BusinessDistrict, Feeder
from django.utils.text import slugify
from django.utils import timezone


class Command(BaseCommand):
    help = 'Populate DailyTechnicalSummary with calculated metrics'

    def add_arguments(self, parser):
        # Date range options
        parser.add_argument(
            '--from-date',
            type=str,
            help='Start date for processing (format: YYYY-MM-DD)'
        )
        parser.add_argument(
            '--to-date',
            type=str,
            help='End date for processing (format: YYYY-MM-DD)'
        )
        parser.add_argument(
            '--today',
            action='store_true',
            help='Process only today'
        )
        parser.add_argument(
            '--yesterday',
            action='store_true',
            help='Process only yesterday'
        )
        parser.add_argument(
            '--last-n-days',
            type=int,
            help='Process last N days'
        )
        parser.add_argument(
            '--current-month',
            action='store_true',
            help='Process all days in current month'
        )
        parser.add_argument(
            '--previous-month',
            action='store_true',
            help='Process all days in previous month'
        )
        
        # Filtering options
        parser.add_argument(
            '--state',
            type=str,
            help='Process only specific state'
        )
        parser.add_argument(
            '--district',
            type=str,
            help='Process only specific business district'
        )
        parser.add_argument(
            '--feeder',
            type=str,
            help='Process only specific feeder (slug)'
        )
        parser.add_argument(
            '--all-levels',
            action='store_true',
            help='Process all aggregation levels (national, state, district, feeder)'
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
            default=30,
            help='Number of days to process in each batch (default: 30)'
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
        
        # Parse filtering options
        filter_configs = self.get_filter_configurations(options)
        
        # Determine which dates to process
        dates_to_process = self.get_dates_to_process(options)
        
        if self.verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Found {len(dates_to_process)} dates to process across {len(filter_configs)} filter configurations'
                )
            )
        
        if options['dry_run']:
            self.show_dry_run(dates_to_process, filter_configs)
            return
        
        # Process dates and filters
        total_processed = 0
        total_skipped = 0
        total_errors = 0
        
        for filter_config in filter_configs:
            processed, skipped, errors = self.process_filter_configuration(
                dates_to_process, filter_config, options
            )
            total_processed += processed
            total_skipped += skipped
            total_errors += errors
        
        if self.verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f'✅ Daily technical summary population completed!\n'
                    f'Processed: {total_processed}, Skipped: {total_skipped}, Errors: {total_errors}'
                )
            )

    def get_filter_configurations(self, options):
        """Get all filter configurations to process"""
        configurations = []
        
        if options['all_levels']:
            # Process all aggregation levels
            
            # National level (no filters)
            configurations.append({
                'state': None,
                'business_district': None,
                'feeder': None,
                'description': 'National'
            })
            
            # State level
            for state in State.objects.all():
                configurations.append({
                    'state': state,
                    'business_district': None,
                    'feeder': None,
                    'description': f'State: {state.name}'
                })
            
            # District level
            for district in BusinessDistrict.objects.select_related('state'):
                configurations.append({
                    'state': None,
                    'business_district': district,
                    'feeder': None,
                    'description': f'District: {district.name} ({district.state.name})'
                })
            
            # Feeder level
            for feeder in Feeder.objects.select_related('business_district__state'):
                configurations.append({
                    'state': None,
                    'business_district': None,
                    'feeder': feeder,
                    'description': f'Feeder: {feeder.name}'
                })
        
        else:
            # Single configuration based on options
            state_obj = None
            district_obj = None
            feeder_obj = None
            
            if options['state']:
                try:
                    state_obj = State.objects.get(name__iexact=options['state'])
                except State.DoesNotExist:
                    raise CommandError(f"State '{options['state']}' not found")
            
            if options['district']:
                try:
                    qs = BusinessDistrict.objects.filter(name__iexact=options['district'])
                    if state_obj:
                        qs = qs.filter(state=state_obj)
                    district_obj = qs.first()
                    if not district_obj:
                        raise CommandError(f"District '{options['district']}' not found")
                except BusinessDistrict.DoesNotExist:
                    raise CommandError(f"District '{options['district']}' not found")
            
            if options['feeder']:
                try:
                    qs = Feeder.objects.filter(slug=options['feeder'])
                    if district_obj:
                        qs = qs.filter(business_district=district_obj)
                    elif state_obj:
                        qs = qs.filter(business_district__state=state_obj)
                    feeder_obj = qs.first()
                    if not feeder_obj:
                        raise CommandError(f"Feeder '{options['feeder']}' not found")
                except Feeder.DoesNotExist:
                    raise CommandError(f"Feeder '{options['feeder']}' not found")
            
            # Determine description
            if feeder_obj:
                description = f"Feeder: {feeder_obj.name}"
            elif district_obj:
                description = f"District: {district_obj.name}"
            elif state_obj:
                description = f"State: {state_obj.name}"
            else:
                description = "National"
            
            configurations.append({
                'state': state_obj,
                'business_district': district_obj,
                'feeder': feeder_obj,
                'description': description
            })
        
        return configurations

    def get_dates_to_process(self, options):
        """Determine which dates need processing based on options"""
        dates = []
        
        if options['today']:
            dates = [date.today()]
        
        elif options['yesterday']:
            dates = [date.today() - timedelta(days=1)]
        
        elif options['from_date'] and options['to_date']:
            try:
                from_date = datetime.strptime(options['from_date'], '%Y-%m-%d').date()
                to_date = datetime.strptime(options['to_date'], '%Y-%m-%d').date()
                
                current = from_date
                while current <= to_date:
                    dates.append(current)
                    current += timedelta(days=1)
            except ValueError:
                raise CommandError('Invalid date format. Use YYYY-MM-DD')
        
        elif options['last_n_days']:
            today = date.today()
            for i in range(options['last_n_days']):
                dates.append(today - timedelta(days=i))
            dates.reverse()  # Process oldest first
        
        elif options['current_month']:
            today = date.today()
            start_of_month = today.replace(day=1)
            current = start_of_month
            while current <= today:
                dates.append(current)
                current += timedelta(days=1)
        
        elif options['previous_month']:
            today = date.today()
            start_of_month = today.replace(day=1)
            start_of_prev_month = start_of_month - timedelta(days=1)
            start_of_prev_month = start_of_prev_month.replace(day=1)
            
            # Last day of previous month
            end_of_prev_month = start_of_month - timedelta(days=1)
            
            current = start_of_prev_month
            while current <= end_of_prev_month:
                dates.append(current)
                current += timedelta(days=1)
        
        else:
            # Default to last 7 days
            today = date.today()
            for i in range(7):
                dates.append(today - timedelta(days=i))
            dates.reverse()
        
        return dates

    def process_filter_configuration(self, dates, filter_config, options):
        """Process all dates for a specific filter configuration"""
        processed = 0
        skipped = 0
        errors = 0
        
        if self.verbosity >= 1:
            self.stdout.write(f'Processing {filter_config["description"]}...')
        
        # Filter dates that need processing
        dates_needing_processing = self.filter_dates_needing_processing(
            dates, filter_config, options
        )
        
        if self.verbosity >= 2:
            self.stdout.write(
                f'  {len(dates_needing_processing)} of {len(dates)} dates need processing'
            )
        
        # Process in batches
        batch_size = options['batch_size']
        for i in range(0, len(dates_needing_processing), batch_size):
            batch = dates_needing_processing[i:i + batch_size]
            batch_processed, batch_skipped, batch_errors = self.process_batch(
                batch, filter_config, options
            )
            processed += batch_processed
            skipped += batch_skipped
            errors += batch_errors
        
        return processed, skipped, errors

    def filter_dates_needing_processing(self, dates, filter_config, options):
        """Filter out dates that already have up-to-date summaries"""
        if options['force']:
            return dates
        
        dates_needing_processing = []
        
        for date_obj in dates:
            try:
                existing = DailyTechnicalSummary.objects.get(
                    date=date_obj,
                    state=filter_config['state'],
                    business_district=filter_config['business_district'],
                    feeder=filter_config['feeder']
                )
                
                if options['check_hash']:
                    # Check if source data has changed
                    current_hash = self.calculate_source_data_hash(date_obj, filter_config)
                    # For now, we'll assume hash checking isn't implemented
                    # You can add hash field to DailyTechnicalSummary if needed
                    dates_needing_processing.append(date_obj)
                else:
                    # For current date, always recalculate if it's older than 1 hour
                    if date_obj == date.today():
                        age = timezone.now() - existing.calculated_at
                        if age > timedelta(hours=1):
                            dates_needing_processing.append(date_obj)
                            if self.verbosity >= 2:
                                self.stdout.write(f"  🔄 {date_obj} - Needs refresh (> 1h old)")
                        elif self.verbosity >= 2:
                            self.stdout.write(f"  ⏭️  {date_obj} - Skipped (recent)")
                    elif self.verbosity >= 2:
                        self.stdout.write(f"  ⏭️  {date_obj} - Skipped (exists)")
            
            except DailyTechnicalSummary.DoesNotExist:
                dates_needing_processing.append(date_obj)
                if self.verbosity >= 2:
                    self.stdout.write(f"  ➕ {date_obj} - New date")
        
        return dates_needing_processing

    def process_batch(self, dates, filter_config, options):
        """Process a batch of dates for a specific filter configuration"""
        processed = 0
        skipped = 0
        errors = 0
        
        for date_obj in dates:
            try:
                success = self.process_single_date(date_obj, filter_config, options)
                if success:
                    processed += 1
                else:
                    skipped += 1
            except Exception as e:
                errors += 1
                if self.verbosity >= 1:
                    self.stdout.write(
                        self.style.ERROR(
                            f'❌ Failed to process {date_obj} for {filter_config["description"]}: {str(e)}'
                        )
                    )
                if options['verbose']:
                    import traceback
                    self.stdout.write(traceback.format_exc())
        
        return processed, skipped, errors

    def process_single_date(self, date_obj, filter_config, options):
        """Process a single date for a specific filter configuration"""
        start_time = time.time()
        
        if self.verbosity >= 2:
            self.stdout.write(f'    Processing {date_obj}...', ending='')
        
        try:
            with transaction.atomic():
                # Use DailyTechnicalCalculator for daily calculations
                from analytics.utils.daily_technical_calculations import DailyTechnicalCalculator
                
                calculator = DailyTechnicalCalculator(
                    target_date=date_obj,
                    state=filter_config['state'],
                    business_district=filter_config['business_district'],
                    feeder=filter_config['feeder']
                )
                
                # Calculate daily metrics
                metrics = calculator.calculate_all_metrics()
                
                # Create or update summary
                summary, created = DailyTechnicalSummary.objects.update_or_create(
                    date=date_obj,
                    state=filter_config['state'],
                    business_district=filter_config['business_district'],
                    feeder=filter_config['feeder'],
                    defaults=metrics
                )
                
                action = "Created" if created else "Updated"
                duration_ms = int((time.time() - start_time) * 1000)
                
                if self.verbosity >= 2:
                    self.stdout.write(f' ✅ {action} ({duration_ms}ms)')
                
                return True
        
        except Exception as e:
            if self.verbosity >= 2:
                self.stdout.write(f' ❌ Error: {str(e)}')
            raise

    def calculate_source_data_hash(self, date_obj, filter_config):
        """Calculate hash of source data to detect changes"""
        # Create a string representation of key source data
        source_data = f"{date_obj}_{filter_config['description']}"
        
        # Add counts from key tables (simplified for now)
        from technical.models import FeederEnergyDaily, HourlyLoad, FeederInterruption
        
        energy_count = FeederEnergyDaily.objects.filter(date=date_obj).count()
        load_count = HourlyLoad.objects.filter(date=date_obj).count()
        interrupt_count = FeederInterruption.objects.filter(occurred_at__date=date_obj).count()
        
        source_data += f"_energy:{energy_count}_load:{load_count}_interrupt:{interrupt_count}"
        
        return hashlib.sha256(source_data.encode()).hexdigest()[:16]

    def show_dry_run(self, dates, filter_configs):
        """Show what would be processed in dry run mode"""
        self.stdout.write(self.style.WARNING('🔍 DRY RUN MODE - No changes will be made'))
        self.stdout.write(f'Would process {len(dates)} dates across {len(filter_configs)} filter configurations:')
        
        for config in filter_configs[:5]:  # Show first 5 configurations
            self.stdout.write(f"  - {config['description']}")
        
        if len(filter_configs) > 5:
            self.stdout.write(f"  ... and {len(filter_configs) - 5} more configurations")
        
        self.stdout.write(f'Date range: {min(dates)} to {max(dates)}')
        self.stdout.write(f'Total operations: {len(dates) * len(filter_configs)}')
            
    def filter_dates_needing_processing(self, dates, filter_config, options):
        """Filter out dates that already have up-to-date summaries"""
        if options['force']:
            return dates
        
        dates_needing_processing = []
        
        for date_obj in dates:
            try:
                existing = DailyTechnicalSummary.objects.get(
                    date=date_obj,
                    state=filter_config['state'],
                    business_district=filter_config['business_district'],
                    feeder=filter_config['feeder']
                )
                
                if options['check_hash']:
                    # Check if source data has changed
                    current_hash = self.calculate_source_data_hash(date_obj, filter_config)
                    # For now, we'll assume hash checking isn't implemented
                    # You can add hash field to DailyTechnicalSummary if needed
                    dates_needing_processing.append(date_obj)
                else:
                    # For current date, always recalculate if it's older than 1 hour
                    if date_obj == date.today():
                        from django.utils import timezone
                        age = timezone.now() - existing.calculated_at
                        if age > timedelta(hours=1):
                            dates_needing_processing.append(date_obj)
                            if self.verbosity >= 2:
                                self.stdout.write(f"  🔄 {date_obj} - Needs refresh (> 1h old)")
                        elif self.verbosity >= 2:
                            self.stdout.write(f"  ⏭️  {date_obj} - Skipped (recent)")
                    elif self.verbosity >= 2:
                        self.stdout.write(f"  ⏭️  {date_obj} - Skipped (exists)")
            
            except DailyTechnicalSummary.DoesNotExist:
                dates_needing_processing.append(date_obj)
                if self.verbosity >= 2:
                    self.stdout.write(f"  ➕ {date_obj} - New date")
        
        return dates_needing_processing