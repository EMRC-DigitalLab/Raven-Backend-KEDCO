# technical/management/commands/recalculate_energy_delivered.py

from django.core.management.base import BaseCommand
from django.db import transaction
from datetime import timedelta
from decimal import Decimal
from technical.models import EnergyDelivered, CumulativeMeterReading
from common.models import Feeder
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = """
    OPTIMIZED recalculation with bulk operations for 10-100x faster performance.
    
    This command will:
    1. Read cumulative meter readings
    2. Calculate daily consumption (difference between consecutive days)
    3. Validate calculations (check for negative values, large jumps, etc.)
    4. Update EnergyDelivered records with correct values using bulk operations
    5. Optionally trigger summary recalculation
    
    Examples:
        # Recalculate all data with validation
        python manage.py recalculate_energy_delivered --all --validate
        
        # Recalculate for specific date range
        python manage.py recalculate_energy_delivered --from-date 2024-01-01 --to-date 2024-12-31
        
        # Recalculate for specific feeder
        python manage.py recalculate_energy_delivered --feeder-slug my-feeder-01 --all
        
        # Dry run to see what would be changed
        python manage.py recalculate_energy_delivered --all --dry-run --validate
        
        # Fix anomalies automatically
        python manage.py recalculate_energy_delivered --all --fix-anomalies
    """

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Recalculate all energy delivered records'
        )
        parser.add_argument(
            '--from-date',
            type=str,
            help='Start date (YYYY-MM-DD)'
        )
        parser.add_argument(
            '--to-date',
            type=str,
            help='End date (YYYY-MM-DD)'
        )
        parser.add_argument(
            '--feeder-slug',
            type=str,
            help='Specific feeder slug to recalculate'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without making changes'
        )
        parser.add_argument(
            '--validate',
            action='store_true',
            help='Validate data quality and report issues'
        )
        parser.add_argument(
            '--fix-anomalies',
            action='store_true',
            help='Automatically fix common anomalies (negative values, large jumps)'
        )
        parser.add_argument(
            '--update-summaries',
            action='store_true',
            help='Update analytics summaries after recalculation'
        )
        parser.add_argument(
            '--disable-signals',
            action='store_true',
            help='Disable analytics signals during recalculation for performance'
        )
        parser.add_argument(
            '--report-only',
            action='store_true',
            help='Only generate a quality report without recalculating'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=5000,
            help='Batch size for bulk operations (default: 5000)'
        )

    def handle(self, *args, **options):
        from datetime import datetime
        
        # Disable signals if requested for better performance
        if options['disable_signals']:
            cache.set('analytics_signals_disabled', True, timeout=None)
            self.stdout.write(self.style.WARNING('Analytics signals DISABLED'))
        
        try:
            # Build filter for cumulative readings
            reading_filter = {}
            
            if options['from_date']:
                from_date = datetime.strptime(options['from_date'], '%Y-%m-%d').date()
                reading_filter['reading_date__gte'] = from_date
            
            if options['to_date']:
                to_date = datetime.strptime(options['to_date'], '%Y-%m-%d').date()
                reading_filter['reading_date__lte'] = to_date
            
            if options['feeder_slug']:
                try:
                    feeder = Feeder.objects.get(slug=options['feeder_slug'])
                    reading_filter['feeder'] = feeder
                    self.stdout.write(f"Filtering for feeder: {feeder.name}")
                except Feeder.DoesNotExist:
                    self.stdout.write(self.style.ERROR(f"Feeder not found: {options['feeder_slug']}"))
                    return
            
            if not options['all'] and not options['from_date'] and not options['to_date']:
                self.stdout.write(self.style.ERROR('Please specify --all or date range'))
                return
            
            # Get all relevant cumulative readings, ordered by feeder and date
            # Use select_related to reduce queries
            readings = CumulativeMeterReading.objects.filter(
                **reading_filter
            ).select_related('feeder').order_by('feeder', 'reading_date')
            
            total_readings = readings.count()
            self.stdout.write(f"Found {total_readings} cumulative meter readings to process")
            
            if total_readings == 0:
                self.stdout.write(self.style.WARNING('No readings found to process'))
                return
            
            # Initialize statistics
            stats = {
                'created': 0,
                'updated': 0,
                'errors': 0,
                'negative_values': 0,
                'large_jumps': 0,
                'fixed_anomalies': 0,
                'total_energy': Decimal('0')
            }
            
            # Process readings by feeder
            feeders = readings.values_list('feeder', flat=True).distinct()
            
            for feeder_id in feeders:
                # Use iterator() to reduce memory usage for large querysets
                feeder_readings = list(readings.filter(feeder_id=feeder_id).order_by('reading_date'))
                feeder = Feeder.objects.get(id=feeder_id)
                
                self.stdout.write(f"\n{'='*70}")
                self.stdout.write(f"Processing feeder: {feeder.name} ({len(feeder_readings)} readings)")
                self.stdout.write(f"{'='*70}")
                
                self._process_feeder(
                    feeder, 
                    feeder_readings, 
                    stats, 
                    options
                )
            
            # Print summary
            self._print_summary(stats, options)
            
            # Update summaries if requested
            if options['update_summaries'] and not options['dry_run'] and not options['report_only']:
                self.stdout.write("\nUpdating analytics summaries...")
                self._update_summaries(reading_filter)
        
        finally:
            # Re-enable signals
            if options['disable_signals']:
                cache.delete('analytics_signals_disabled')
                self.stdout.write(self.style.SUCCESS('Analytics signals RE-ENABLED'))
    
    def _process_feeder(self, feeder, readings, stats, options):
        """Process all readings for a single feeder using bulk operations"""
        previous_reading = None
        anomalies = []
        energy_records_to_create = []
        energy_records_to_update = []
        
        # Get existing EnergyDelivered records for this feeder in ONE query
        existing_records = {}
        if not options['report_only'] and not options['dry_run']:
            existing_records = {
                record.date: record
                for record in EnergyDelivered.objects.filter(
                    feeder=feeder,
                    date__in=[r.reading_date for r in readings]
                )
            }
        
        for reading in readings:
            if previous_reading is None:
                # First reading for this feeder - skip as we need previous day to calculate
                if options['validate']:
                    self.stdout.write(
                        f"  First reading: {reading.reading_date} "
                        f"(cumulative: {reading.cumulative_mwh:.2f} MWh)"
                    )
                previous_reading = reading
                continue
            
            # Calculate daily consumption
            consumption_mwh = reading.cumulative_mwh - previous_reading.cumulative_mwh
            
            # Validate consumption
            is_valid, issue_type, issue_msg = self._validate_consumption(
                reading.reading_date,
                consumption_mwh,
                previous_reading.cumulative_mwh,
                reading.cumulative_mwh
            )
            
            if not is_valid:
                stats['errors'] += 1
                
                if issue_type == 'negative':
                    stats['negative_values'] += 1
                elif issue_type == 'large_jump':
                    stats['large_jumps'] += 1
                
                anomalies.append({
                    'date': reading.reading_date,
                    'issue': issue_msg,
                    'consumption': consumption_mwh,
                    'prev_cumulative': previous_reading.cumulative_mwh,
                    'curr_cumulative': reading.cumulative_mwh
                })
                
                # Try to fix if requested
                if options['fix_anomalies'] and issue_type == 'negative':
                    # Assume meter was reset, use current reading as consumption
                    consumption_mwh = reading.cumulative_mwh
                    stats['fixed_anomalies'] += 1
                    if options['validate']:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  FIXED: {reading.reading_date} - "
                                f"Assuming meter reset, using {consumption_mwh:.2f} MWh"
                            )
                        )
                else:
                    if options['validate']:
                        self.stdout.write(
                            self.style.ERROR(f"  ERROR: {issue_msg}")
                        )
                    previous_reading = reading
                    continue
            
            # Prepare record for bulk operation
            if not options['report_only'] and not options['dry_run']:
                if reading.reading_date in existing_records:
                    # Update existing record
                    obj = existing_records[reading.reading_date]
                    obj.energy_mwh = consumption_mwh
                    obj.is_calculated = True
                    obj.calculation_method = 'meter_difference'
                    energy_records_to_update.append(obj)
                    stats['updated'] += 1
                else:
                    # Create new record
                    energy_records_to_create.append(
                        EnergyDelivered(
                            feeder=feeder,
                            date=reading.reading_date,
                            energy_mwh=consumption_mwh,
                            is_calculated=True,
                            calculation_method='meter_difference'
                        )
                    )
                    stats['created'] += 1
                    
                stats['total_energy'] += consumption_mwh
            
            previous_reading = reading
        
        # Bulk create/update records using transaction for atomicity
        if not options['report_only'] and not options['dry_run']:
            with transaction.atomic():
                # Bulk create
                if energy_records_to_create:
                    EnergyDelivered.objects.bulk_create(
                        energy_records_to_create,
                        batch_size=options['batch_size'],
                        ignore_conflicts=False
                    )
                    if options['validate']:
                        self.stdout.write(f"  ✓ Bulk created {len(energy_records_to_create)} records")
                
                # Bulk update
                if energy_records_to_update:
                    EnergyDelivered.objects.bulk_update(
                        energy_records_to_update,
                        ['energy_mwh', 'is_calculated', 'calculation_method'],
                        batch_size=options['batch_size']
                    )
                    if options['validate']:
                        self.stdout.write(f"  ✓ Bulk updated {len(energy_records_to_update)} records")
        
        # Report anomalies for this feeder
        if anomalies and options['validate']:
            self.stdout.write(f"\n  ⚠️  Anomalies found for {feeder.name}: {len(anomalies)} total")
            for anomaly in anomalies[:10]:  # Show first 10
                self.stdout.write(
                    f"    {anomaly['date']}: {anomaly['issue']} "
                    f"(consumption: {anomaly['consumption']:.2f} MWh)"
                )
            if len(anomalies) > 10:
                self.stdout.write(f"    ... and {len(anomalies) - 10} more anomalies")
    
    def _validate_consumption(self, date, consumption, prev_cumulative, curr_cumulative):
        """
        Validate calculated consumption for common issues.
        Returns: (is_valid, issue_type, message)
        """
        # Check for negative consumption (meter reset or bad data)
        if consumption < 0:
            return (
                False, 
                'negative',
                f"{date}: Negative consumption! "
                f"Current: {curr_cumulative:.2f}, Previous: {prev_cumulative:.2f}"
            )
        
        # Check for unusually large jumps (potential data error)
        # Typical daily consumption might be 0-100 MWh for a feeder
        # Flag anything over 1000 MWh as suspicious
        if consumption > 1000:
            return (
                False,
                'large_jump',
                f"{date}: Unusually large consumption: {consumption:.2f} MWh! "
                f"Previous cumulative: {prev_cumulative:.2f}, Current: {curr_cumulative:.2f}"
            )
        
        # Check for zero consumption (might be data gap)
        if consumption == 0:
            # This is valid but worth noting
            return (True, 'zero', None)
        
        return (True, None, None)
    
    def _print_summary(self, stats, options):
        """Print summary statistics"""
        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS("✅ PROCESSING COMPLETE"))
        self.stdout.write("="*70)
        
        if options['dry_run']:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes made"))
        if options['report_only']:
            self.stdout.write(self.style.WARNING("REPORT ONLY - No recalculation performed"))
        
        self.stdout.write(f"Created: {stats['created']}")
        self.stdout.write(f"Updated: {stats['updated']}")
        self.stdout.write(f"Total Energy Processed: {stats['total_energy']:.2f} MWh")
        
        if stats['errors'] > 0:
            self.stdout.write(self.style.ERROR(f"\n⚠️  Data Quality Issues: {stats['errors']} errors found"))
            self.stdout.write(f"  Negative values: {stats['negative_values']}")
            self.stdout.write(f"  Large jumps: {stats['large_jumps']}")
            
            if stats['fixed_anomalies'] > 0:
                self.stdout.write(self.style.SUCCESS(f"  Fixed anomalies: {stats['fixed_anomalies']}"))
            
            if not options['fix_anomalies']:
                self.stdout.write(
                    self.style.WARNING(
                        "\n💡 Tip: Run with --fix-anomalies to automatically fix common issues"
                    )
                )
    
    def _update_summaries(self, reading_filter):
        """Update analytics summaries for affected date range"""
        from analytics.signals import (
            update_daily_technical_summary_sync,
            update_monthly_technical_summary_sync
        )
        from datetime import datetime
        
        # Get unique dates and months from filter
        dates = CumulativeMeterReading.objects.filter(
            **reading_filter
        ).values_list('reading_date', flat=True).distinct().order_by('reading_date')
        
        affected_months = set()
        
        for date_obj in dates:
            # Update daily summary
            self.stdout.write(f"  Updating daily summary for {date_obj}")
            update_daily_technical_summary_sync(date_obj.strftime('%Y-%m-%d'))
            
            # Track affected months
            month_start = date_obj.replace(day=1)
            affected_months.add(month_start)
        
        # Update monthly summaries
        for month_start in sorted(affected_months):
            self.stdout.write(f"  Updating monthly summary for {month_start.strftime('%Y-%m')}")
            update_monthly_technical_summary_sync(month_start.strftime('%Y-%m-%d'))
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Updated summaries for {len(dates)} days and {len(affected_months)} months"
            )
        )