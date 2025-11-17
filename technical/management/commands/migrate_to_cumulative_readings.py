# technical/management/commands/migrate_to_cumulative_readings.py

from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal
from datetime import datetime, timedelta
from technical.models import EnergyDelivered, CumulativeMeterReading
from common.models import Feeder
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = """
    IMPROVED migration that handles both scenarios:
    1. Pre-August data: EnergyDelivered contains DAILY CONSUMPTION (differences)
    2. Post-August data: EnergyDelivered contains CUMULATIVE READINGS
    
    This command will:
    1. Identify which records are cumulative vs daily consumption
    2. For daily consumption: Build cumulative readings by summing forward
    3. For cumulative readings: Copy directly to CumulativeMeterReading
    4. Handle the transition point carefully
    
    Examples:
        # Migrate all data with automatic detection
        python manage.py migrate_to_cumulative_readings_v2 --all --dry-run
        
        # Migrate with explicit cutoff date
        python manage.py migrate_to_cumulative_readings_v2 --all --cutoff-date 2024-08-01
        
        # Migrate specific feeder
        python manage.py migrate_to_cumulative_readings_v2 --feeder-slug my-feeder-01 --all
    """

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Migrate all energy delivered records'
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
            help='Specific feeder slug to migrate'
        )
        parser.add_argument(
            '--cutoff-date',
            type=str,
            default='2024-08-01',
            help='Date when data changed from daily to cumulative (default: 2024-08-01)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be migrated without making changes'
        )
        parser.add_argument(
            '--auto-detect',
            action='store_true',
            help='Automatically detect the transition point by analyzing data patterns'
        )
        parser.add_argument(
            '--recalculate-after',
            action='store_true',
            help='Run recalculation command after migration'
        )

    def handle(self, *args, **options):
        # Parse cutoff date
        cutoff_date = datetime.strptime(options['cutoff_date'], '%Y-%m-%d').date()
        self.stdout.write(f"Using cutoff date: {cutoff_date}")
        self.stdout.write(f"  Before {cutoff_date}: Treating as DAILY CONSUMPTION")
        self.stdout.write(f"  From {cutoff_date} onward: Treating as CUMULATIVE READINGS")
        
        # Build filter
        energy_filter = {}
        
        if options['from_date']:
            from_date = datetime.strptime(options['from_date'], '%Y-%m-%d').date()
            energy_filter['date__gte'] = from_date
        
        if options['to_date']:
            to_date = datetime.strptime(options['to_date'], '%Y-%m-%d').date()
            energy_filter['date__lte'] = to_date
        
        if options['feeder_slug']:
            try:
                feeder = Feeder.objects.get(slug=options['feeder_slug'])
                energy_filter['feeder'] = feeder
                self.stdout.write(f"Filtering for feeder: {feeder.name}")
            except Feeder.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Feeder not found: {options['feeder_slug']}"))
                return
        
        if not options['all'] and not options['from_date'] and not options['to_date']:
            self.stdout.write(self.style.ERROR('Please specify --all or date range'))
            return
        
        # Auto-detect cutoff if requested
        if options['auto_detect']:
            detected_cutoff = self._auto_detect_cutoff(energy_filter)
            if detected_cutoff:
                cutoff_date = detected_cutoff
                self.stdout.write(self.style.SUCCESS(f"Auto-detected cutoff date: {cutoff_date}"))
        
        # Get all feeders to process
        if options['feeder_slug']:
            feeders = [Feeder.objects.get(slug=options['feeder_slug'])]
        else:
            feeder_ids = EnergyDelivered.objects.filter(**energy_filter).values_list('feeder_id', flat=True).distinct()
            feeders = Feeder.objects.filter(id__in=feeder_ids)
        
        self.stdout.write(f"\nProcessing {feeders.count()} feeders...")
        
        total_created = 0
        total_skipped = 0
        
        for feeder in feeders:
            self.stdout.write(f"\n{'='*70}")
            self.stdout.write(f"Processing feeder: {feeder.name}")
            self.stdout.write(f"{'='*70}")
            
            created, skipped = self._migrate_feeder_data(
                feeder, 
                cutoff_date, 
                energy_filter, 
                options['dry_run']
            )
            
            total_created += created
            total_skipped += skipped
        
        # Summary
        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS("MIGRATION COMPLETE"))
        self.stdout.write("="*70)
        if options['dry_run']:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes made"))
        self.stdout.write(f"Total created: {total_created}")
        self.stdout.write(f"Total skipped: {total_skipped}")
        
        # Run recalculation if requested
        if options['recalculate_after'] and not options['dry_run']:
            self.stdout.write("\n" + "="*70)
            self.stdout.write("Running recalculation command...")
            
            from django.core.management import call_command
            
            recalc_args = ['--disable-signals', '--update-summaries']
            if options['all']:
                recalc_args.append('--all')
            if options['from_date']:
                recalc_args.extend(['--from-date', options['from_date']])
            if options['to_date']:
                recalc_args.extend(['--to-date', options['to_date']])
            if options['feeder_slug']:
                recalc_args.extend(['--feeder-slug', options['feeder_slug']])
            
            call_command('recalculate_energy_delivered', *recalc_args)
    
    def _auto_detect_cutoff(self, energy_filter):
        """
        Auto-detect the cutoff date by analyzing data patterns.
        Cumulative data should show monotonically increasing values,
        while daily consumption will have smaller values that fluctuate.
        """
        from django.db.models import F, Window
        from django.db.models.functions import Lag
        
        self.stdout.write("\nAuto-detecting cutoff date...")
        
        # Get a sample of data and look for the transition point
        # Look for dates where values suddenly jump significantly
        feeders = EnergyDelivered.objects.filter(**energy_filter).values_list('feeder_id', flat=True).distinct()[:5]
        
        potential_cutoffs = []
        
        for feeder_id in feeders:
            records = EnergyDelivered.objects.filter(
                feeder_id=feeder_id,
                **energy_filter
            ).order_by('date').values('date', 'energy_mwh')
            
            prev_value = None
            for record in records:
                current_value = float(record['energy_mwh'])
                
                if prev_value is not None:
                    # Look for sudden large increases that might indicate switch to cumulative
                    ratio = current_value / prev_value if prev_value > 0 else 0
                    
                    # If value increases by more than 10x, likely switched to cumulative
                    if ratio > 10:
                        potential_cutoffs.append(record['date'])
                        self.stdout.write(
                            f"  Potential cutoff detected on {record['date']} "
                            f"(jump from {prev_value:.2f} to {current_value:.2f})"
                        )
                        break
                
                prev_value = current_value
        
        if potential_cutoffs:
            # Use the earliest detected cutoff
            cutoff = min(potential_cutoffs)
            return cutoff
        
        self.stdout.write(self.style.WARNING("  Could not auto-detect cutoff, using default"))
        return None
    
    def _migrate_feeder_data(self, feeder, cutoff_date, base_filter, dry_run):
        """
        Migrate data for a single feeder, handling the transition from daily to cumulative.
        """
        # Get all records for this feeder
        feeder_filter = {**base_filter, 'feeder': feeder}
        records = EnergyDelivered.objects.filter(**feeder_filter).order_by('date')
        
        if not records.exists():
            return 0, 0
        
        created_count = 0
        skipped_count = 0
        
        # Split records into pre-cutoff and post-cutoff
        pre_cutoff = records.filter(date__lt=cutoff_date).order_by('date')
        post_cutoff = records.filter(date__gte=cutoff_date).order_by('date')
        
        self.stdout.write(f"  Pre-cutoff records (daily consumption): {pre_cutoff.count()}")
        self.stdout.write(f"  Post-cutoff records (cumulative): {post_cutoff.count()}")
        
        # Process pre-cutoff data (daily consumption -> build cumulative)
        if pre_cutoff.exists():
            self.stdout.write("\n  Processing pre-cutoff data (building cumulative from daily)...")
            running_total = Decimal('0')
            
            for record in pre_cutoff:
                # Add daily consumption to running total
                running_total += record.energy_mwh
                
                if not dry_run:
                    cumulative_reading, created = CumulativeMeterReading.objects.get_or_create(
                        feeder=feeder,
                        reading_date=record.date,
                        defaults={
                            'cumulative_mwh': running_total,
                            'is_estimated': False,
                            'notes': 'Migrated from daily consumption (pre-cutoff)'
                        }
                    )
                    
                    if created:
                        created_count += 1
                        if created_count <= 5:  # Show first 5
                            self.stdout.write(
                                f"    {record.date}: Daily={record.energy_mwh:.2f} MWh, "
                                f"Cumulative={running_total:.2f} MWh"
                            )
                    else:
                        skipped_count += 1
                else:
                    exists = CumulativeMeterReading.objects.filter(
                        feeder=feeder,
                        reading_date=record.date
                    ).exists()
                    
                    if not exists:
                        created_count += 1
                        if created_count <= 5:
                            self.stdout.write(
                                f"    WOULD CREATE: {record.date}: Daily={record.energy_mwh:.2f} MWh, "
                                f"Cumulative={running_total:.2f} MWh"
                            )
                    else:
                        skipped_count += 1
            
            # Store the final cumulative value for transition
            final_pre_cutoff_cumulative = running_total
            self.stdout.write(
                f"  Final pre-cutoff cumulative: {final_pre_cutoff_cumulative:.2f} MWh"
            )
        else:
            final_pre_cutoff_cumulative = Decimal('0')
        
        # Process post-cutoff data (already cumulative)
        if post_cutoff.exists():
            self.stdout.write("\n  Processing post-cutoff data (copying cumulative readings)...")
            
            first_post_cutoff = post_cutoff.first()
            
            # Check if we need to adjust for baseline
            # The first post-cutoff reading might need to be offset by pre-cutoff total
            adjustment_needed = False
            if final_pre_cutoff_cumulative > 0:
                # If first post-cutoff value is less than final pre-cutoff,
                # the meter was likely reset, so we need to add the offset
                if first_post_cutoff.energy_mwh < final_pre_cutoff_cumulative:
                    adjustment_needed = True
                    self.stdout.write(
                        self.style.WARNING(
                            f"    Meter appears to have been reset at cutoff. "
                            f"Will add offset of {final_pre_cutoff_cumulative:.2f} MWh"
                        )
                    )
            
            for record in post_cutoff:
                # Use cumulative value directly, with adjustment if needed
                if adjustment_needed:
                    cumulative_mwh = record.energy_mwh + final_pre_cutoff_cumulative
                else:
                    cumulative_mwh = record.energy_mwh
                
                if not dry_run:
                    cumulative_reading, created = CumulativeMeterReading.objects.get_or_create(
                        feeder=feeder,
                        reading_date=record.date,
                        defaults={
                            'cumulative_mwh': cumulative_mwh,
                            'is_estimated': False,
                            'notes': 'Migrated from cumulative reading (post-cutoff)'
                        }
                    )
                    
                    if created:
                        created_count += 1
                        if created_count <= 5:  # Show first 5
                            self.stdout.write(
                                f"    {record.date}: Cumulative={cumulative_mwh:.2f} MWh"
                            )
                    else:
                        skipped_count += 1
                else:
                    exists = CumulativeMeterReading.objects.filter(
                        feeder=feeder,
                        reading_date=record.date
                    ).exists()
                    
                    if not exists:
                        created_count += 1
                        if created_count <= 5:
                            self.stdout.write(
                                f"    WOULD CREATE: {record.date}: Cumulative={cumulative_mwh:.2f} MWh"
                            )
                    else:
                        skipped_count += 1
        
        self.stdout.write(f"\n  Feeder summary: Created={created_count}, Skipped={skipped_count}")
        return created_count, skipped_count