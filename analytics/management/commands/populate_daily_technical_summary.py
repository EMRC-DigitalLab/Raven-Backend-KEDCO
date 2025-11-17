# analytics/management/commands/populate_daily_technical_summary.py
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum, Count, Avg, Q
from django.db import transaction
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from decimal import Decimal
import hashlib
import time

from analytics.models import DailyTechnicalSummary
from analytics.utils.daily_technical_calculations import DailyTechnicalCalculator
from common.models import State, BusinessDistrict, Feeder
from django.utils.text import slugify
from django.utils import timezone


class Command(BaseCommand):
    help = 'Populate DailyTechnicalSummary with calculated metrics (OPTIMIZED with bulk operations)'

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
            default=100,
            help='Number of summaries to create/update in bulk (default: 100)'
        )
        parser.add_argument(
            '--use-bulk',
            action='store_true',
            help='Use optimized bulk operations (much faster for many records)'
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
        if options['use_bulk'] and len(dates_to_process) * len(filter_configs) > 10:
            self.process_all_bulk(dates_to_process, filter_configs, options)
        else:
            self.process_all_sequential(dates_to_process, filter_configs, options)

    def get_filter_configurations(self, options):
        """Get all filter configurations to process - OPTIMIZED"""
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
            
            # Pre-fetch all entities in single queries
            states = list(State.objects.all())
            districts = list(BusinessDistrict.objects.select_related('state').all())
            feeders = list(Feeder.objects.select_related('business_district__state').all())
            
            # State level
            for state in states:
                configurations.append({
                    'state': state,
                    'business_district': None,
                    'feeder': None,
                    'description': f'State: {state.name}'
                })
            
            # District level
            for district in districts:
                configurations.append({
                    'state': None,
                    'business_district': district,
                    'feeder': None,
                    'description': f'District: {district.name} ({district.state.name})'
                })
            
            # Feeder level
            for feeder in feeders:
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
                qs = BusinessDistrict.objects.filter(name__iexact=options['district'])
                if state_obj:
                    qs = qs.filter(state=state_obj)
                district_obj = qs.first()
                if not district_obj:
                    raise CommandError(f"District '{options['district']}' not found")
            
            if options['feeder']:
                qs = Feeder.objects.filter(slug=options['feeder'])
                if district_obj:
                    qs = qs.filter(business_district=district_obj)
                elif state_obj:
                    qs = qs.filter(business_district__state=state_obj)
                feeder_obj = qs.first()
                if not feeder_obj:
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

    def process_all_bulk(self, dates, filter_configs, options):
        """Process all dates and filters using bulk operations - OPTIMIZED"""
        total_operations = len(dates) * len(filter_configs)
        
        if self.verbosity >= 1:
            self.stdout.write(f'Using BULK mode for {total_operations} operations...')
        
        start_time = time.time()
        
        # Pre-fetch all existing summaries in ONE query
        existing_summaries = {}
        if not options['force']:
            # Build Q object for all combinations
            q_objects = Q()
            for date_obj in dates:
                for config in filter_configs:
                    q_objects |= Q(
                        date=date_obj,
                        state=config['state'],
                        business_district=config['business_district'],
                        feeder=config['feeder']
                    )
            
            for summary in DailyTechnicalSummary.objects.filter(q_objects):
                key = (
                    summary.date,
                    summary.state_id,
                    summary.business_district_id,
                    summary.feeder_id
                )
                existing_summaries[key] = summary
        
        # Process all combinations
        to_create = []
        to_update = []
        skipped = 0
        errors = 0
        
        total_calc_time = 0
        
        for date_obj in dates:
            date_start = time.time()
            
            for config in filter_configs:
                try:
                    # Create lookup key
                    key = (
                        date_obj,
                        config['state'].id if config['state'] else None,
                        config['business_district'].id if config['business_district'] else None,
                        config['feeder'].id if config['feeder'] else None
                    )
                    
                    # Check if exists
                    existing = existing_summaries.get(key)
                    
                    # Skip logic
                    if existing and not options['force']:
                        # For current date, check if older than 1 hour
                        if date_obj == date.today():
                            age = timezone.now() - existing.calculated_at
                            if age <= timedelta(hours=1):
                                skipped += 1
                                continue
                        else:
                            skipped += 1
                            continue
                    
                    # Calculate metrics
                    calc_start = time.time()
                    calculator = DailyTechnicalCalculator(
                        target_date=date_obj,
                        state=config['state'],
                        business_district=config['business_district'],
                        feeder=config['feeder']
                    )
                    metrics = calculator.calculate_all_metrics()
                    total_calc_time += (time.time() - calc_start)
                    
                    if existing:
                        # Update existing
                        for key_name, value in metrics.items():
                            setattr(existing, key_name, value)
                        to_update.append(existing)
                    else:
                        # Create new
                        to_create.append(DailyTechnicalSummary(
                            date=date_obj,
                            state=config['state'],
                            business_district=config['business_district'],
                            feeder=config['feeder'],
                            **metrics
                        ))
                
                except Exception as e:
                    errors += 1
                    if self.verbosity >= 1:
                        self.stderr.write(
                            f'Error processing {date_obj} - {config["description"]}: {str(e)}'
                        )
            
            if self.verbosity >= 1:
                date_time = time.time() - date_start
                processed = len(to_create) + len(to_update) + skipped
                self.stdout.write(
                    f'{date_obj}: Processed {processed} configs in {date_time:.1f}s'
                )
        
        # Execute bulk operations
        db_start = time.time()
        created_count = 0
        updated_count = 0
        
        with transaction.atomic():
            # Bulk create
            if to_create:
                DailyTechnicalSummary.objects.bulk_create(
                    to_create,
                    batch_size=options['batch_size']
                )
                created_count = len(to_create)
                if self.verbosity >= 1:
                    self.stdout.write(f'✅ Bulk created {created_count} summaries')
            
            # Bulk update
            if to_update:
                # Get all field names except primary key and timestamp fields
                update_fields = [
                    f.name for f in DailyTechnicalSummary._meta.fields
                    if f.name not in ['id', 'created_at', 'date', 'state', 'business_district', 'feeder']
                ]
                
                DailyTechnicalSummary.objects.bulk_update(
                    to_update,
                    update_fields,
                    batch_size=options['batch_size']
                )
                updated_count = len(to_update)
                if self.verbosity >= 1:
                    self.stdout.write(f'✅ Bulk updated {updated_count} summaries')
        
        db_time = time.time() - db_start
        total_time = time.time() - start_time
        
        # Final summary
        self.stdout.write(f'\n{"="*60}')
        self.stdout.write(f'Final Results:')
        self.stdout.write(f'  Created: {created_count}')
        self.stdout.write(f'  Updated: {updated_count}')
        self.stdout.write(f'  Skipped: {skipped}')
        self.stdout.write(f'  Errors: {errors}')
        self.stdout.write(f'\nPerformance:')
        self.stdout.write(f'  Total time: {total_time:.1f}s')
        self.stdout.write(f'  Calculation time: {total_calc_time:.1f}s ({total_calc_time/total_time*100:.1f}%)')
        self.stdout.write(f'  Database time: {db_time:.1f}s ({db_time/total_time*100:.1f}%)')
        
        total_processed = created_count + updated_count
        if total_processed > 0:
            self.stdout.write(f'  Avg time per summary: {total_time/total_processed:.3f}s')
            self.stdout.write(f'  Throughput: {total_processed/total_time:.1f} summaries/sec')
        self.stdout.write(f'{"="*60}')

    def process_all_sequential(self, dates, filter_configs, options):
        """Process all dates and filters sequentially"""
        total_processed = 0
        total_skipped = 0
        total_errors = 0
        
        for filter_config in filter_configs:
            processed, skipped, errors = self.process_filter_configuration(
                dates, filter_config, options
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
        
        # Process dates
        for date_obj in dates_needing_processing:
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
        
        return processed, skipped, errors

    def filter_dates_needing_processing(self, dates, filter_config, options):
        """Filter out dates that already have up-to-date summaries - OPTIMIZED"""
        if options['force']:
            return dates
        
        # Fetch all existing summaries for this filter config in ONE query
        existing_dates = set(
            DailyTechnicalSummary.objects.filter(
                date__in=dates,
                state=filter_config['state'],
                business_district=filter_config['business_district'],
                feeder=filter_config['feeder']
            ).values_list('date', flat=True)
        )
        
        dates_needing_processing = []
        
        for date_obj in dates:
            if date_obj not in existing_dates:
                dates_needing_processing.append(date_obj)
                if self.verbosity >= 2:
                    self.stdout.write(f"  ➕ {date_obj} - New date")
            elif date_obj == date.today():
                # For today, check if needs refresh (> 1 hour old)
                existing = DailyTechnicalSummary.objects.get(
                    date=date_obj,
                    state=filter_config['state'],
                    business_district=filter_config['business_district'],
                    feeder=filter_config['feeder']
                )
                age = timezone.now() - existing.calculated_at
                if age > timedelta(hours=1):
                    dates_needing_processing.append(date_obj)
                    if self.verbosity >= 2:
                        self.stdout.write(f"  🔄 {date_obj} - Needs refresh (> 1h old)")
                elif self.verbosity >= 2:
                    self.stdout.write(f"  ⏭️  {date_obj} - Skipped (recent)")
            elif self.verbosity >= 2:
                self.stdout.write(f"  ⏭️  {date_obj} - Skipped (exists)")
        
        return dates_needing_processing

    def process_single_date(self, date_obj, filter_config, options):
        """Process a single date for a specific filter configuration"""
        start_time = time.time()
        
        if self.verbosity >= 2:
            self.stdout.write(f'    Processing {date_obj}...', ending='')
        
        try:
            with transaction.atomic():
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