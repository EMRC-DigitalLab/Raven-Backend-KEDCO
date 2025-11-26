# technical/management/commands/import_hourly_load.py
"""
Management command to import hourly load data from external MySQL database.

Usage:
    python manage.py import_hourly_load
    python manage.py import_hourly_load --start-date 2025-01-01
    python manage.py import_hourly_load --feeder-slug feeder-name-slug
    python manage.py import_hourly_load --dry-run
"""

import mysql.connector
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from common.models import Feeder
from technical.models import HourlyLoad


class Command(BaseCommand):
    help = 'Import hourly load data from external MySQL database (ONBOARDED FEEDERS ONLY)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-date',
            type=str,
            help='Start date for import (YYYY-MM-DD). Default: all historical data',
        )
        parser.add_argument(
            '--end-date',
            type=str,
            help='End date for import (YYYY-MM-DD). Default: today',
        )
        parser.add_argument(
            '--feeder-slug',
            type=str,
            help='Import data for specific feeder only (by slug)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without saving data (for testing)',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=10000,
            help='Batch size for bulk operations (default: 10000)',
        )
        parser.add_argument(
            '--skip-validation',
            action='store_true',
            help='Skip hour validation (0-23 range check)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting hourly load data import...'))
        
        # Parse options
        start_date = options.get('start_date')
        end_date = options.get('end_date')
        feeder_slug = options.get('feeder_slug')
        dry_run = options.get('dry_run', False)
        batch_size = options.get('batch_size', 10000)
        skip_validation = options.get('skip_validation', False)
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No data will be saved'))
        
        # Statistics
        stats = {
            'total_records': 0,
            'numeric_values': 0,
            'fault_values_skipped': 0,
            'loads_created': 0,
            'loads_updated': 0,
            'loads_skipped': 0,
            'invalid_hours': 0,
            'negative_loads': 0,
            'zero_loads': 0,
            'feeders_not_found': set(),
            'feeders_not_onboarded': set(),
        }
        
        # Pre-load ONBOARDED feeders into a dictionary for fast lookup
        self.stdout.write('Loading ONBOARDED feeders...')
        feeder_map = {
            f.slug: f 
            for f in Feeder.objects.filter(is_onboarded=True)
        }
        self.stdout.write(f'Loaded {len(feeder_map)} ONBOARDED feeders')
        
        if len(feeder_map) == 0:
            self.stdout.write(self.style.ERROR(
                'No onboarded feeders found! Please onboard feeders before importing data.'
            ))
            return
        
        # Load existing load records for faster duplicate checking
        self.stdout.write('Loading existing hourly load records...')
        existing_loads = set()
        
        # Only load for onboarded feeders to save memory
        onboarded_feeder_ids = [f.id for f in feeder_map.values()]
        for load in HourlyLoad.objects.filter(
            feeder_id__in=onboarded_feeder_ids
        ).values_list('feeder_id', 'date', 'hour'):
            existing_loads.add((load[0], load[1], load[2]))
        
        self.stdout.write(f'Loaded {len(existing_loads)} existing load records')
        
        # Lists for bulk operations
        loads_to_create = []
        loads_to_update = []
        
        try:
            # Connect to external database
            self.stdout.write('Connecting to external database...')
            connection = mysql.connector.connect(
                host='31.97.56.29',
                user='root',
                password='EMRC-Password-123#',
                database='dataNestDB_KEDCO',
                port=3306
            )
            cursor = connection.cursor(dictionary=True)
            
            # Build query
            query = """
                SELECT 
                    feeder_id,
                    Date,
                    Hour_d,
                    LoadS
                FROM Technicalhourlydata
                WHERE 1=1
            """
            params = []
            
            if start_date:
                query += " AND Date >= %s"
                params.append(start_date)
            
            if end_date:
                query += " AND Date <= %s"
                params.append(end_date)
            
            if feeder_slug:
                query += " AND feeder_id = %s"
                params.append(feeder_slug)
            else:
                # Only query for onboarded feeders
                feeder_slugs = list(feeder_map.keys())
                if feeder_slugs:
                    placeholders = ','.join(['%s'] * len(feeder_slugs))
                    query += f" AND feeder_id IN ({placeholders})"
                    params.extend(feeder_slugs)
            
            # Order by date and hour for efficient processing
            query += " ORDER BY feeder_id, Date, Hour_d"
            
            self.stdout.write(f'Executing query...')
            cursor.execute(query, params)
            
            # Process records
            for row in cursor:
                stats['total_records'] += 1
                
                feeder_slug_val = row['feeder_id']
                date = row['Date']
                hour = row['Hour_d']
                load_value = row['LoadS']
                
                # Get feeder from pre-loaded map (already filtered for onboarded)
                feeder = feeder_map.get(feeder_slug_val)
                if not feeder:
                    if feeder_slug_val not in stats['feeders_not_onboarded']:
                        # Check if feeder exists but is not onboarded
                        if Feeder.objects.filter(slug=feeder_slug_val).exists():
                            stats['feeders_not_onboarded'].add(feeder_slug_val)
                        else:
                            stats['feeders_not_found'].add(feeder_slug_val)
                    continue
                
                # Check if this is a numeric value (actual load) or fault
                if not self._is_numeric(load_value):
                    stats['fault_values_skipped'] += 1
                    continue
                
                stats['numeric_values'] += 1
                
                # Convert to Decimal
                try:
                    load_mw = Decimal(str(load_value))
                except (InvalidOperation, ValueError, TypeError):
                    stats['fault_values_skipped'] += 1
                    continue
                
                # Validate hour range
                if not skip_validation:
                    if hour < 0 or hour > 23:
                        stats['invalid_hours'] += 1
                        self.stdout.write(self.style.WARNING(
                            f'Invalid hour {hour} for {feeder_slug_val} on {date}'
                        ))
                        continue
                
                # Track zero and negative loads (but still import them)
                if load_mw < 0:
                    stats['negative_loads'] += 1
                elif load_mw == 0:
                    stats['zero_loads'] += 1
                
                # Check if this load record already exists
                key = (feeder.id, date, hour)
                
                if key in existing_loads:
                    # Already exists - check if we need to update
                    existing = HourlyLoad.objects.filter(
                        feeder=feeder,
                        date=date,
                        hour=hour
                    ).first()
                    
                    if existing and existing.load_mw != load_mw:
                        # Value changed - update it
                        existing.load_mw = load_mw
                        loads_to_update.append(existing)
                    else:
                        stats['loads_skipped'] += 1
                else:
                    # New load record - add to create list
                    loads_to_create.append(
                        HourlyLoad(
                            feeder=feeder,
                            date=date,
                            hour=hour,
                            load_mw=load_mw
                        )
                    )
                    # Add to existing set to prevent duplicates within same batch
                    existing_loads.add(key)
                
                # Progress indicator
                if stats['total_records'] % 50000 == 0:
                    self.stdout.write(
                        f"Processed {stats['total_records']:,} records, "
                        f"{stats['numeric_values']:,} numeric values, "
                        f"{len(loads_to_create):,} to create..."
                    )
                
                # Batch save to avoid memory issues
                if len(loads_to_create) >= batch_size:
                    if not dry_run:
                        self._bulk_create_loads(loads_to_create, stats)
                    else:
                        stats['loads_created'] += len(loads_to_create)
                    loads_to_create = []
                
                if len(loads_to_update) >= batch_size:
                    if not dry_run:
                        self._bulk_update_loads(loads_to_update, stats)
                    else:
                        stats['loads_updated'] += len(loads_to_update)
                    loads_to_update = []
            
            # Save remaining loads
            if loads_to_create:
                if not dry_run:
                    self._bulk_create_loads(loads_to_create, stats)
                else:
                    stats['loads_created'] += len(loads_to_create)
            
            if loads_to_update:
                if not dry_run:
                    self._bulk_update_loads(loads_to_update, stats)
                else:
                    stats['loads_updated'] += len(loads_to_update)
            
            cursor.close()
            connection.close()
            
            # Print statistics
            self.stdout.write(self.style.SUCCESS('\n=== Import Complete ==='))
            self.stdout.write(f"Total records processed: {stats['total_records']:,}")
            self.stdout.write(f"Numeric values (load readings): {stats['numeric_values']:,}")
            self.stdout.write(f"Fault values skipped: {stats['fault_values_skipped']:,}")
            self.stdout.write(f"\nLoad records created: {stats['loads_created']:,}")
            self.stdout.write(f"Load records updated: {stats['loads_updated']:,}")
            self.stdout.write(f"Load records skipped (duplicates): {stats['loads_skipped']:,}")
            
            # Data quality metrics
            self.stdout.write(self.style.WARNING('\n=== Data Quality ==='))
            self.stdout.write(f"Zero load values: {stats['zero_loads']:,}")
            self.stdout.write(f"Negative load values: {stats['negative_loads']:,}")
            if not skip_validation:
                self.stdout.write(f"Invalid hours (not 0-23): {stats['invalid_hours']:,}")
            
            if stats['feeders_not_onboarded']:
                self.stdout.write(self.style.WARNING(
                    f"\nFeeders not onboarded ({len(stats['feeders_not_onboarded'])}): "
                    f"{', '.join(sorted(list(stats['feeders_not_onboarded'])[:20]))}"
                    f"{'...' if len(stats['feeders_not_onboarded']) > 20 else ''}"
                ))
                self.stdout.write(
                    "These feeders exist in the database but are not onboarded. "
                    "Onboard them in the admin to import their data."
                )
            
            if stats['feeders_not_found']:
                self.stdout.write(self.style.WARNING(
                    f"\nFeeders not found in database ({len(stats['feeders_not_found'])}): "
                    f"{', '.join(sorted(list(stats['feeders_not_found'])[:20]))}"
                    f"{'...' if len(stats['feeders_not_found']) > 20 else ''}"
                ))
            
        except mysql.connector.Error as err:
            self.stdout.write(self.style.ERROR(f'Database error: {err}'))
            raise
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {e}'))
            import traceback
            traceback.print_exc()
            raise

    def _is_numeric(self, value):
        """Check if a value is numeric (indicating actual load reading)"""
        if value is None or value == '':
            return False
        try:
            float(value)
            return True
        except (ValueError, TypeError):
            return False

    def _bulk_create_loads(self, loads, stats):
        """Bulk create load records"""
        try:
            HourlyLoad.objects.bulk_create(loads, batch_size=1000, ignore_conflicts=True)
            stats['loads_created'] += len(loads)
            self.stdout.write(f"Created {len(loads):,} load records")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error creating loads: {e}'))
            # Try one by one if bulk fails
            for load in loads:
                try:
                    load.save()
                    stats['loads_created'] += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(
                        f"Failed to create load for {load.feeder.slug} "
                        f"on {load.date} hour {load.hour}: {e}"
                    ))

    def _bulk_update_loads(self, loads, stats):
        """Bulk update load records"""
        try:
            HourlyLoad.objects.bulk_update(loads, ['load_mw'], batch_size=1000)
            stats['loads_updated'] += len(loads)
            self.stdout.write(f"Updated {len(loads):,} load records")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error updating loads: {e}'))
            # Try one by one if bulk fails
            for load in loads:
                try:
                    load.save()
                    stats['loads_updated'] += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(
                        f"Failed to update load for {load.feeder.slug} "
                        f"on {load.date} hour {load.hour}: {e}"
                    ))