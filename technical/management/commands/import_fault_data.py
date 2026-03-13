# technical/management/commands/import_fault_data.py
"""
Management command to import fault/interruption data from external MySQL database.

Usage:
    python manage.py import_fault_data
    python manage.py import_fault_data --start-date 2025-01-01
    python manage.py import_fault_data --feeder-slug feeder-name-slug
"""

from collections import defaultdict
from datetime import datetime, timedelta

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from common.models import Feeder
from technical.models import FeederInterruption


class Command(BaseCommand):
    help = 'Import fault/interruption data from external MySQL database'

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
            default=5000,
            help='Batch size for bulk operations (default: 5000)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting fault data import...'))
        
        # Parse options
        start_date = options.get('start_date')
        end_date = options.get('end_date')
        feeder_slug = options.get('feeder_slug')
        dry_run = options.get('dry_run', False)
        batch_size = options.get('batch_size', 5000)
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No data will be saved'))
        
        # Statistics
        stats = {
            'total_records': 0,
            'faults_detected': 0,
            'interruptions_created': 0,
            'interruptions_updated': 0,
            'interruptions_skipped': 0,
            'feeders_not_found': set(),
            'unknown_fault_types': set(),
        }
        
        # Pre-load all feeders into a dictionary for fast lookup
        self.stdout.write('Loading feeders...')
        feeder_map = {f.slug: f for f in Feeder.objects.all()}
        self.stdout.write(f'Loaded {len(feeder_map)} feeders')
        
        # Load existing interruptions for faster duplicate checking
        self.stdout.write('Loading existing interruptions...')
        existing_interruptions = set()
        for interruption in FeederInterruption.objects.values_list('feeder_id', 'occurred_at', 'interruption_type'):
            existing_interruptions.add((interruption[0], interruption[1], interruption[2]))
        self.stdout.write(f'Loaded {len(existing_interruptions)} existing interruptions')
        
        # Lists for bulk operations
        interruptions_to_create = []
        interruptions_to_update = []
        
        try:
            # Connect to external database
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
            
            # Order by feeder first, then by date and hour to process sequentially
            query += " ORDER BY feeder_id, Date, Hour_d"
            
            self.stdout.write(f'Executing query...')
            cursor.execute(query, params)
            
            # Track ongoing faults per feeder
            # Key: feeder_slug, Value: fault data dict
            ongoing_faults = {}
            
            # Completed faults ready to be saved
            completed_faults = []
            
            for row in cursor:
                stats['total_records'] += 1
                
                feeder_slug_val = row['feeder_id']
                date = row['Date']
                hour = row['Hour_d']
                load_value = row['LoadS']
                
                # Get feeder from pre-loaded map
                feeder = feeder_map.get(feeder_slug_val)
                if not feeder:
                    stats['feeders_not_found'].add(feeder_slug_val)
                    # If feeder changes and we had an ongoing fault, resolve it
                    if feeder_slug_val in ongoing_faults:
                        del ongoing_faults[feeder_slug_val]
                    continue
                
                # Create datetime for this hour
                current_time = timezone.make_aware(
                    datetime.combine(date, datetime.min.time()) + timedelta(hours=hour)
                )
                
                # Check if this is a fault or normal operation
                is_fault = not self._is_numeric(load_value)
                
                if is_fault:
                    stats['faults_detected'] += 1
                    fault_type = str(load_value).strip()
                    
                    # Map fault type to our choices
                    mapped_fault_type = self._map_fault_type(fault_type)
                    if mapped_fault_type == 'N/A' and fault_type not in ['N/A', '', None]:
                        stats['unknown_fault_types'].add(fault_type)
                    
                    # Check if we have an ongoing fault for this feeder
                    if feeder_slug_val in ongoing_faults:
                        ongoing_fault = ongoing_faults[feeder_slug_val]
                        
                        if ongoing_fault['fault_type'] == mapped_fault_type:
                            # Same fault type - just extend duration
                            ongoing_fault['latest_time'] = current_time
                        else:
                            # DIFFERENT fault type - resolve the old one first, then start new
                            # The old fault was resolved at the start of this hour
                            ongoing_fault['restored_at'] = current_time
                            completed_faults.append(ongoing_fault)
                            
                            # Start new fault
                            ongoing_faults[feeder_slug_val] = {
                                'feeder': feeder,
                                'feeder_slug': feeder_slug_val,
                                'fault_type': mapped_fault_type,
                                'occurred_at': current_time,
                                'latest_time': current_time,
                            }
                    else:
                        # No ongoing fault - start new one
                        ongoing_faults[feeder_slug_val] = {
                            'feeder': feeder,
                            'feeder_slug': feeder_slug_val,
                            'fault_type': mapped_fault_type,
                            'occurred_at': current_time,
                            'latest_time': current_time,
                        }
                else:
                    # Normal operation (numeric value) - resolve any ongoing fault
                    if feeder_slug_val in ongoing_faults:
                        ongoing_fault = ongoing_faults[feeder_slug_val]
                        ongoing_fault['restored_at'] = current_time
                        completed_faults.append(ongoing_fault)
                        del ongoing_faults[feeder_slug_val]
                
                # Progress indicator
                if stats['total_records'] % 10000 == 0:
                    self.stdout.write(f"Processed {stats['total_records']} records, {len(completed_faults)} faults completed...")
                
                # Batch save to avoid memory issues
                if len(completed_faults) >= batch_size:
                    self._save_batch(
                        completed_faults, 
                        existing_interruptions, 
                        interruptions_to_create, 
                        interruptions_to_update, 
                        stats, 
                        dry_run
                    )
                    completed_faults = []
            
            # Handle any remaining ongoing faults at the end (still unresolved)
            for feeder_slug_val, ongoing_fault in ongoing_faults.items():
                # These faults are still ongoing (no restoration time)
                completed_faults.append(ongoing_fault)
            
            # Save remaining completed faults
            if completed_faults:
                self._save_batch(
                    completed_faults, 
                    existing_interruptions, 
                    interruptions_to_create, 
                    interruptions_to_update, 
                    stats, 
                    dry_run
                )
            
            # Bulk create new interruptions
            if interruptions_to_create and not dry_run:
                self.stdout.write(f'Bulk creating {len(interruptions_to_create)} interruptions...')
                FeederInterruption.objects.bulk_create(interruptions_to_create, batch_size=1000)
                stats['interruptions_created'] = len(interruptions_to_create)
            
            # Bulk update existing interruptions
            if interruptions_to_update and not dry_run:
                self.stdout.write(f'Bulk updating {len(interruptions_to_update)} interruptions...')
                FeederInterruption.objects.bulk_update(
                    interruptions_to_update, 
                    ['restored_at'], 
                    batch_size=1000
                )
                stats['interruptions_updated'] = len(interruptions_to_update)
            
            cursor.close()
            connection.close()
            
            # Print statistics
            self.stdout.write(self.style.SUCCESS('\n=== Import Complete ==='))
            self.stdout.write(f"Total records processed: {stats['total_records']}")
            self.stdout.write(f"Faults detected: {stats['faults_detected']}")
            self.stdout.write(f"Interruptions created: {len(interruptions_to_create)}")
            self.stdout.write(f"Interruptions updated: {len(interruptions_to_update)}")
            self.stdout.write(f"Interruptions skipped (duplicates): {stats['interruptions_skipped']}")
            
            if stats['feeders_not_found']:
                self.stdout.write(self.style.WARNING(
                    f"\nFeeders not found ({len(stats['feeders_not_found'])}): "
                    f"{', '.join(sorted(list(stats['feeders_not_found'])[:20]))}"
                    f"{'...' if len(stats['feeders_not_found']) > 20 else ''}"
                ))
            
            if stats['unknown_fault_types']:
                self.stdout.write(self.style.WARNING(
                    f"\nUnknown fault types mapped to N/A ({len(stats['unknown_fault_types'])}): "
                    f"{', '.join(sorted(stats['unknown_fault_types']))}"
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
        """Check if a value is numeric (indicating normal operation)"""
        if value is None or value == '':
            return False
        try:
            float(value)
            return True
        except (ValueError, TypeError):
            return False

    def _map_fault_type(self, fault_type):
        """Map fault type from external DB to our interruption types"""
        # Normalize the fault type
        fault_type = str(fault_type).strip().upper()
        
        # Direct mapping
        fault_map = {
            'O/C': 'O/C',
            'E/F': 'E/F',
            'O/C & E/F': 'O/C & E/F',
            'OC & E/F': 'OC & E/F',
            'NO RI': 'NO RI',
            'L/S': 'L/S',
            'O/S': 'O/S',
            'T/F': 'T/F',
            'B/F': 'B/F',
            'O/N': 'O/N',
            'O/E': 'O/E',
            'P/O': 'P/O',
            'O/F': 'O/F',
            'P/M': 'P/M',
            'O': 'O',
            'T/S': 'T/S',
            'L/S GS': 'L/S GS',
            'MTNC': 'MTNC',
            'MTCE': 'MTCE',
            'EM/D': 'EM/D',
            '330KV L/F': '330KV L/F',
            'OFF': 'OFF',
            'S/C': 'S/C',
            '132KV E/F': '132KV E/F',
            '132KV L/F': '132KV L/F',
            '330KV L/S': '330KV L/S',
            '132KV CB/F': '132KV CB/F',
            'D/C': 'D/C',
            'IN O/C': 'IN O/C',
            'T/LS': 'T/LS',
            '132KV MTCE': '132KV MTCE',
            'LIM': 'LIM',
            'TCN': 'tcn',
            'FAULT': 'fault',
            'PERMIT': 'permit',
            'L/F': 'L/F',
        }
        
        return fault_map.get(fault_type, 'N/A')

    def _save_batch(self, completed_faults, existing_interruptions, interruptions_to_create, interruptions_to_update, stats, dry_run):
        """Process a batch of completed faults"""
        for fault_data in completed_faults:
            feeder = fault_data['feeder']
            fault_type = fault_data['fault_type']
            occurred_at = fault_data['occurred_at']
            restored_at = fault_data.get('restored_at')
            
            # Check if this interruption already exists
            key = (feeder.id, occurred_at, fault_type)
            
            if key in existing_interruptions:
                # Already exists - check if we need to update restoration time
                if restored_at:
                    existing = FeederInterruption.objects.filter(
                        feeder=feeder,
                        occurred_at=occurred_at,
                        interruption_type=fault_type,
                        restored_at__isnull=True
                    ).first()
                    
                    if existing:
                        existing.restored_at = restored_at
                        interruptions_to_update.append(existing)
                    else:
                        stats['interruptions_skipped'] += 1
                else:
                    stats['interruptions_skipped'] += 1
            else:
                # New interruption - add to create list
                interruptions_to_create.append(
                    FeederInterruption(
                        feeder=feeder,
                        interruption_type=fault_type,
                        occurred_at=occurred_at,
                        restored_at=restored_at,
                        description="Imported from external database"
                    )
                )
                # Add to existing set to prevent duplicates within same batch
                existing_interruptions.add(key)