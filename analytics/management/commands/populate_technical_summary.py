# analytics/management/commands/populate_technical_summary.py
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import transaction, connection
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta #type: ignore
import time

from analytics.models import MonthlyTechnicalSummary
from analytics.utils.technical_calculations import TechnicalCalculator
from common.models import State, BusinessDistrict, Feeder


class Command(BaseCommand):
    help = 'Populate or update monthly technical summary data (OPTIMIZED with bulk operations)'

    def add_arguments(self, parser):
        # Date arguments
        parser.add_argument(
            '--month',
            type=str,
            help='Specific month to populate (YYYY-MM format, e.g., 2025-07)'
        )
        parser.add_argument(
            '--current-month',
            action='store_true',
            help='Populate only the current month'
        )
        parser.add_argument(
            '--start-year',
            type=int,
            help='Starting year for bulk population'
        )
        parser.add_argument(
            '--end-year',
            type=int,
            help='Ending year for bulk population'
        )
        parser.add_argument(
            '--from-date',
            type=str,
            help='Start date for range (YYYY-MM-DD format)'
        )
        parser.add_argument(
            '--to-date',
            type=str,
            help='End date for range (YYYY-MM-DD format)'
        )

        # Filtering arguments
        parser.add_argument(
            '--state',
            type=str,
            help='Filter by state name'
        )
        parser.add_argument(
            '--district',
            type=str,
            help='Filter by business district name'
        )
        parser.add_argument(
            '--feeder',
            type=str,
            help='Filter by feeder slug'
        )
        parser.add_argument(
            '--all-levels',
            action='store_true',
            help='Populate all filtering levels (national, state, district, feeder)'
        )

        # Behavior arguments
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force recalculation even if summary already exists'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually doing it'
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

    def handle(self, *args, **options):
        self.verbosity = options['verbosity']
        self.dry_run = options['dry_run']
        self.force = options['force']
        self.batch_size = options['batch_size']
        self.use_bulk = options['use_bulk']

        if self.dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN MODE - No changes will be made')
            )

        # Check if we're in an atomic block (ATOMIC_REQUESTS enabled)
        if connection.in_atomic_block:
            self.stdout.write(
                self.style.WARNING(
                    '⚠️  WARNING: Running inside an atomic transaction block!\n'
                    '   This might be due to ATOMIC_REQUESTS in settings.\n'
                    '   Data will only be visible after the ENTIRE command completes.\n'
                )
            )

        try:
            # Parse date parameters
            months = self._parse_date_params(options)
            
            # Parse filter parameters
            filter_configs = self._parse_filter_params(options)
            
            # Show summary of what will be done
            self._show_execution_plan(months, filter_configs)
            
            if not self.dry_run:
                # Execute the population
                if self.use_bulk and len(months) * len(filter_configs) > 10:
                    self._populate_summaries_bulk(months, filter_configs)
                else:
                    self._populate_summaries(months, filter_configs)
            
            self.stdout.write(
                self.style.SUCCESS('✅ Technical summary population completed successfully')
            )

        except Exception as e:
            raise CommandError(f'Error populating technical summaries: {str(e)}')

    def _parse_date_params(self, options):
        """Parse date parameters and return list of months to process"""
        if options['month']:
            try:
                month_date = datetime.strptime(options['month'], '%Y-%m').date().replace(day=1)
                return [month_date]
            except ValueError:
                raise CommandError('Invalid month format. Use YYYY-MM (e.g., 2025-07)')

        if options['current_month']:
            return [date.today().replace(day=1)]

        if options['from_date'] and options['to_date']:
            try:
                from_date = datetime.strptime(options['from_date'], '%Y-%m-%d').date().replace(day=1)
                to_date = datetime.strptime(options['to_date'], '%Y-%m-%d').date().replace(day=1)
                
                months = []
                current = from_date
                while current <= to_date:
                    months.append(current)
                    current += relativedelta(months=1)
                return months
            except ValueError:
                raise CommandError('Invalid date format. Use YYYY-MM-DD')

        if options['start_year'] or options['end_year']:
            current_year = date.today().year
            start_year = options['start_year'] or current_year
            end_year = options['end_year'] or current_year
            
            months = []
            for year in range(start_year, end_year + 1):
                for month in range(1, 13):
                    month_date = date(year, month, 1)
                    # Don't process future months
                    if month_date <= date.today().replace(day=1):
                        months.append(month_date)
            return months

        # Default: current month only
        return [date.today().replace(day=1)]

    def _parse_filter_params(self, options):
        """Parse filter parameters and return list of filter configurations"""
        filter_configs = []

        if options['all_levels']:
            # Generate all possible filtering combinations
            filter_configs.extend(self._generate_all_filter_combinations())
        else:
            # Single filter configuration based on provided parameters
            config = {
                'state': None,
                'business_district': None,
                'feeder': None
            }

            # Parse state
            if options['state']:
                try:
                    config['state'] = State.objects.get(name__iexact=options['state'])
                except State.DoesNotExist:
                    raise CommandError(f'State "{options["state"]}" not found')

            # Parse district
            if options['district']:
                qs = BusinessDistrict.objects.filter(name__iexact=options['district'])
                if config['state']:
                    qs = qs.filter(state=config['state'])
                
                district = qs.first()
                if not district:
                    raise CommandError(f'Business district "{options["district"]}" not found')
                config['business_district'] = district

            # Parse feeder
            if options['feeder']:
                qs = Feeder.objects.filter(slug=options['feeder'])
                if config['business_district']:
                    qs = qs.filter(business_district=config['business_district'])
                elif config['state']:
                    qs = qs.filter(business_district__state=config['state'])
                
                feeder = qs.first()
                if not feeder:
                    raise CommandError(f'Feeder "{options["feeder"]}" not found')
                config['feeder'] = feeder

            filter_configs.append(config)

        return filter_configs

    def _generate_all_filter_combinations(self):
        """Generate all possible filter combinations - OPTIMIZED"""
        configs = []

        # Voltages to process for aggregate levels
        voltages = ['11kv', '33kv']

        for voltage in voltages:
            # National level
            configs.append({
                'state': None,
                'business_district': None,
                'feeder': None,
                'feeder_type': voltage
            })

        # Pre-fetch all relationships in single queries - ONLY ONBOARDED AND ACTIVE
        states = list(State.objects.all())
        districts = list(BusinessDistrict.objects.select_related('state').all())
        feeders = list(Feeder.objects.filter(is_onboarded=True, status='active').select_related('business_district__state').all())

        for voltage in voltages:
            # State level
            for state in states:
                configs.append({
                    'state': state,
                    'business_district': None,
                    'feeder': None,
                    'feeder_type': voltage
                })

            # District level
            for district in districts:
                configs.append({
                    'state': district.state,
                    'business_district': district,
                    'feeder': None,
                    'feeder_type': voltage
                })

        # Feeder level - feeder_type must match feeder's voltage_level
        for feeder in feeders:
            if feeder.business_district:
                configs.append({
                    'state': feeder.business_district.state,
                    'business_district': feeder.business_district,
                    'feeder': feeder,
                    'feeder_type': feeder.voltage_level
                })

        return configs

    def _show_execution_plan(self, months, filter_configs):
        """Display what will be processed"""
        total_operations = len(months) * len(filter_configs)
        
        self.stdout.write(f'\nExecution Plan:')
        self.stdout.write(f'  Months to process: {len(months)}')
        self.stdout.write(f'  Filter configurations: {len(filter_configs)}')
        self.stdout.write(f'  Total operations: {total_operations}')
        
        if self.use_bulk:
            self.stdout.write(self.style.SUCCESS(f'  Mode: BULK (optimized)'))
        
        if self.verbosity >= 2:
            self.stdout.write('\nMonths:')
            for month in months[:5]:  # Show first 5
                self.stdout.write(f'  - {month.strftime("%Y-%m")}')
            if len(months) > 5:
                self.stdout.write(f'  ... and {len(months) - 5} more')
            
            self.stdout.write('\nFilter configurations:')
            for i, config in enumerate(filter_configs[:5]):  # Show first 5
                desc = self._get_filter_description(config)
                self.stdout.write(f'  - {desc}')
            if len(filter_configs) > 5:
                self.stdout.write(f'  ... and {len(filter_configs) - 5} more')

        # Check existing summaries - OPTIMIZED
        if not self.force:
            existing_count = self._count_existing_summaries_bulk(months, filter_configs)
            if existing_count > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f'\n{existing_count} summaries already exist and will be skipped. '
                        'Use --force to recalculate them.'
                    )
                )

        self.stdout.write('')

    def _count_existing_summaries_bulk(self, months, filter_configs):
        """Count existing summaries using a single optimized query"""
        from django.db.models import Q
        
        # Build Q object for all combinations
        q_objects = Q()
        for month in months:
            for config in filter_configs:
                q_objects |= Q(
                    month=month,
                    state=config['state'],
                    business_district=config['business_district'],
                    feeder=config['feeder'],
                    feeder_type=config.get('feeder_type', '11kv')
                )
        
        return MonthlyTechnicalSummary.objects.filter(q_objects).count()

    def _force_commit(self):
        """Force a database commit - handles different Django configurations"""
        try:
            # If we're in autocommit mode, this does nothing (which is fine)
            # If we're not, this commits the current transaction
            if not connection.in_atomic_block:
                # Force the connection to commit any pending changes
                connection.commit()
        except Exception:
            # If commit() isn't available or fails, try closing and reopening
            pass

    def _populate_summaries_bulk(self, months, filter_configs):
        """Execute bulk population - OPTIMIZED VERSION with FORCED incremental commits"""
        total_operations = len(months) * len(filter_configs)
        
        start_time = time.time()
        
        if self.verbosity >= 1:
            self.stdout.write(f'Using BULK mode for {total_operations} operations...')
        
        # Pre-fetch existing summaries in ONE query
        existing_summaries = {}
        if not self.force:
            existing_qs = MonthlyTechnicalSummary.objects.all()
            for summary in existing_qs:
                key = (
                    summary.month,
                    summary.state_id,
                    summary.business_district_id,
                    summary.feeder_id,
                    summary.feeder_type
                )
                existing_summaries[key] = summary
        
        # Process all combinations
        to_create = []
        to_update = []
        skipped = 0
        errors = 0
        
        total_calc_time = 0
        
        for month in months:
            month_start = time.time()
            
            for config in filter_configs:
                try:
                    # Create lookup key
                    key = (
                        month,
                        config['state'].id if config['state'] else None,
                        config['business_district'].id if config['business_district'] else None,
                        config['feeder'].id if config['feeder'] else None,
                        config.get('feeder_type', '11kv')
                    )
                    
                    # Check if exists
                    existing = existing_summaries.get(key)
                    
                    if existing and not self.force:
                        skipped += 1
                        continue
                    
                    # Calculate metrics
                    calc_start = time.time()
                    calculator = TechnicalCalculator(
                        month_date=month,
                        state=config['state'],
                        business_district=config['business_district'],
                        feeder=config['feeder'],
                        feeder_type=config.get('feeder_type')
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
                        to_create.append(MonthlyTechnicalSummary(
                            month=month,
                            state=config['state'],
                            business_district=config['business_district'],
                            feeder=config['feeder'],
                            feeder_type=config.get('feeder_type', '11kv'),
                            **metrics
                        ))
                
                except Exception as e:
                    errors += 1
                    if self.verbosity >= 1:
                        filter_desc = self._get_filter_description(config)
                        self.stderr.write(
                            f'Error processing {month.strftime("%Y-%m")} - {filter_desc}: {str(e)}'
                        )
            
            if self.verbosity >= 1:
                month_time = time.time() - month_start
                processed = len(to_create) + len(to_update) + skipped
                self.stdout.write(
                    f'{month.strftime("%Y-%m")}: Processed {processed} configs in {month_time:.1f}s'
                )
        
        # Execute bulk operations with FORCED incremental commits
        db_start = time.time()
        created_count = 0
        updated_count = 0
        
        # Bulk create in batches with FORCED commits
        if to_create:
            total_to_create = len(to_create)
            for i in range(0, total_to_create, self.batch_size):
                batch = to_create[i:i + self.batch_size]
                
                # Use atomic block for this batch
                with transaction.atomic():
                    MonthlyTechnicalSummary.objects.bulk_create(batch)
                    created_count += len(batch)
                
                # FORCE commit immediately after atomic block
                self._force_commit()
                
                if self.verbosity >= 1:
                    self.stdout.write(
                        f'✅ Created batch: {created_count}/{total_to_create} summaries (COMMITTED)',
                        ending='\r'
                    )
                
                # Verify data is in database
                if self.verbosity >= 2 and created_count % (self.batch_size * 5) == 0:
                    actual_count = MonthlyTechnicalSummary.objects.count()
                    self.stdout.write(f'\n   [DEBUG] Total records in DB: {actual_count}')
            
            if self.verbosity >= 1:
                self.stdout.write(f'\n✅ Bulk created {created_count} summaries (ALL COMMITTED)          ')
            
        # Bulk update in batches with FORCED commits
        if to_update:
            # Get all field names from the model
            update_fields = [
                f.name for f in MonthlyTechnicalSummary._meta.fields
                if f.name not in ['id', 'created_at', 'month', 'state', 'business_district', 'feeder']
            ]
            
            total_to_update = len(to_update)
            for i in range(0, total_to_update, self.batch_size):
                batch = to_update[i:i + self.batch_size]
                
                # Use atomic block for this batch
                with transaction.atomic():
                    MonthlyTechnicalSummary.objects.bulk_update(
                        batch,
                        update_fields
                    )
                    updated_count += len(batch)
                
                # FORCE commit immediately after atomic block
                self._force_commit()
                
                if self.verbosity >= 1:
                    self.stdout.write(
                        f'✅ Updated batch: {updated_count}/{total_to_update} summaries (COMMITTED)',
                        ending='\r'
                    )
            
            if self.verbosity >= 1:
                self.stdout.write(f'\n✅ Bulk updated {updated_count} summaries (ALL COMMITTED)          ')
        
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
        
        # Final verification
        if self.verbosity >= 1:
            final_count = MonthlyTechnicalSummary.objects.count()
            self.stdout.write(f'\n✅ Final verification: {final_count} total records in database')

    def _populate_summaries(self, months, filter_configs):
        """Execute the population (non-bulk version)"""
        total_operations = len(months) * len(filter_configs)
        processed = 0
        created = 0
        updated = 0
        skipped = 0
        errors = 0

        start_time = time.time()

        for month in months:
            month_start_time = time.time()
            month_processed = 0
            month_created = 0
            month_updated = 0
            month_skipped = 0
            month_errors = 0

            for config in filter_configs:
                try:
                    result = self._process_single_summary(month, config)
                    
                    if result == 'created':
                        created += 1
                        month_created += 1
                    elif result == 'updated':
                        updated += 1
                        month_updated += 1
                    elif result == 'skipped':
                        skipped += 1
                        month_skipped += 1
                    
                    month_processed += 1
                    processed += 1

                except Exception as e:
                    errors += 1
                    month_errors += 1
                    
                    if self.verbosity >= 1:
                        filter_desc = self._get_filter_description(config)
                        self.stderr.write(
                            f'Error processing {month.strftime("%Y-%m")} - {filter_desc}: {str(e)}'
                        )

                # Progress reporting
                if processed % 50 == 0 or processed == total_operations:
                    self._show_progress(processed, total_operations, start_time)

            # Month summary
            if self.verbosity >= 1:
                month_time = time.time() - month_start_time
                self.stdout.write(
                    f'{month.strftime("%Y-%m")}: '
                    f'{month_created} created, {month_updated} updated, '
                    f'{month_skipped} skipped, {month_errors} errors '
                    f'({month_time:.1f}s)'
                )

        # Final summary
        total_time = time.time() - start_time
        self.stdout.write(f'\nFinal Results:')
        self.stdout.write(f'  Total processed: {processed}')
        self.stdout.write(f'  Created: {created}')
        self.stdout.write(f'  Updated: {updated}')
        self.stdout.write(f'  Skipped: {skipped}')
        self.stdout.write(f'  Errors: {errors}')
        self.stdout.write(f'  Total time: {total_time:.1f}s')
        
        if processed > 0:
            self.stdout.write(f'  Average time per summary: {total_time/processed:.2f}s')

    def _process_single_summary(self, month, config):
        """Process a single month/filter combination"""
        # Check if summary exists
        existing_summary = MonthlyTechnicalSummary.objects.filter(
            month=month,
            state=config['state'],
            business_district=config['business_district'],
            feeder=config['feeder'],
            feeder_type=config.get('feeder_type', '11kv')
        ).first()

        if existing_summary and not self.force:
            return 'skipped'

        # Calculate metrics
        calculator = TechnicalCalculator(
            month_date=month,
            state=config['state'],
            business_district=config['business_district'],
            feeder=config['feeder'],
            feeder_type=config.get('feeder_type')
        )

        metrics = calculator.calculate_all_metrics()

        # Create or update summary
        if existing_summary:
            # Update existing
            for key, value in metrics.items():
                setattr(existing_summary, key, value)
            existing_summary.save()
            return 'updated'
        else:
            # Create new
            MonthlyTechnicalSummary.objects.create(
                month=month,
                state=config['state'],
                business_district=config['business_district'],
                feeder=config['feeder'],
                feeder_type=config.get('feeder_type', '11kv'),
                **metrics
            )
            return 'created'

    def _get_filter_description(self, config):
        """Get human-readable description of filter configuration"""
        if config['feeder']:
            return f"Feeder: {config['feeder'].slug}"
        elif config['business_district']:
            return f"District: {config['business_district'].name}"
        elif config['state']:
            return f"State: {config['state'].name}"
        else:
            return "National"

    def _show_progress(self, processed, total, start_time):
        """Show progress information"""
        if total > 0:
            percentage = (processed / total) * 100
            elapsed = time.time() - start_time
            
            if processed > 0:
                avg_time = elapsed / processed
                remaining_time = avg_time * (total - processed)
                eta = f", ETA: {remaining_time:.0f}s"
            else:
                eta = ""
            
            self.stdout.write(
                f'Progress: {processed}/{total} ({percentage:.1f}%) '
                f'- {elapsed:.1f}s elapsed{eta}',
                ending='\r'
            )
            
            if processed == total:
                self.stdout.write('')  # New line at end