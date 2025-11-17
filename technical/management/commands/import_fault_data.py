# technical/management/commands/import_fault_data.py
"""
Management command to import fault/interruption data from external MySQL database.

Usage:
    python manage.py import_fault_data
    python manage.py import_fault_data --start-date 2025-01-01
    python manage.py import_fault_data --feeder-slug feeder-name-slug
"""

import mysql.connector
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from datetime import datetime, timedelta
from decimal import Decimal
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

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting fault data import...'))
        
        # Parse options
        start_date = options.get('start_date')
        end_date = options.get('end_date')
        feeder_slug = options.get('feeder_slug')
        dry_run = options.get('dry_run', False)
        
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
                    LoadS,
                    Hour
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
            
            query += " ORDER BY feeder_id, Date, Hour_d"
            
            self.stdout.write(f'Executing query with params: {params}')
            cursor.execute(query, params)
            
            # Process records
            current_fault = None
            
            for row in cursor:
                stats['total_records'] += 1
                
                feeder_slug = row['feeder_id']
                date = row['Date']
                hour = row['Hour_d']
                load_value = row['LoadS']
                
                # Try to get the feeder
                try:
                    feeder = Feeder.objects.get(slug=feeder_slug)
                except Feeder.DoesNotExist:
                    stats['feeders_not_found'].add(feeder_slug)
                    if current_fault and current_fault['feeder_slug'] == feeder_slug:
                        current_fault = None
                    continue
                
                # Create datetime for this hour
                occurred_at = timezone.make_aware(
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
                    
                    # Check if we're tracking an ongoing fault
                    if current_fault:
                        # If it's the same feeder and fault type, extend the duration
                        if (current_fault['feeder'] == feeder and 
                            current_fault['fault_type'] == mapped_fault_type):
                            # Still ongoing, update the latest occurrence time
                            current_fault['latest_time'] = occurred_at
                        else:
                            # Different fault or different feeder, save the previous one
                            self._save_interruption(current_fault, stats, dry_run)
                            # Start tracking new fault
                            current_fault = {
                                'feeder': feeder,
                                'feeder_slug': feeder_slug,
                                'fault_type': mapped_fault_type,
                                'occurred_at': occurred_at,
                                'latest_time': occurred_at,
                            }
                    else:
                        # New fault started
                        current_fault = {
                            'feeder': feeder,
                            'feeder_slug': feeder_slug,
                            'fault_type': mapped_fault_type,
                            'occurred_at': occurred_at,
                            'latest_time': occurred_at,
                        }
                else:
                    # Normal operation (numeric value)
                    if current_fault and current_fault['feeder'] == feeder:
                        # Fault has been resolved
                        current_fault['restored_at'] = occurred_at
                        self._save_interruption(current_fault, stats, dry_run)
                        current_fault = None
                
                # Progress indicator
                if stats['total_records'] % 1000 == 0:
                    self.stdout.write(f"Processed {stats['total_records']} records...")
            
            # Handle any ongoing fault at the end
            if current_fault:
                # Fault is still ongoing (no restoration time)
                self._save_interruption(current_fault, stats, dry_run)
            
            cursor.close()
            connection.close()
            
            # Print statistics
            self.stdout.write(self.style.SUCCESS('\n=== Import Complete ==='))
            self.stdout.write(f"Total records processed: {stats['total_records']}")
            self.stdout.write(f"Faults detected: {stats['faults_detected']}")
            self.stdout.write(f"Interruptions created: {stats['interruptions_created']}")
            self.stdout.write(f"Interruptions updated: {stats['interruptions_updated']}")
            self.stdout.write(f"Interruptions skipped (duplicates): {stats['interruptions_skipped']}")
            
            if stats['feeders_not_found']:
                self.stdout.write(self.style.WARNING(
                    f"\nFeeders not found ({len(stats['feeders_not_found'])}): "
                    f"{', '.join(sorted(stats['feeders_not_found']))}"
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
        }
        
        return fault_map.get(fault_type, 'N/A')

    def _save_interruption(self, fault_data, stats, dry_run=False):
        """Save or update a FeederInterruption record"""
        feeder = fault_data['feeder']
        fault_type = fault_data['fault_type']
        occurred_at = fault_data['occurred_at']
        restored_at = fault_data.get('restored_at')
        
        # Check if this interruption already exists
        existing = FeederInterruption.objects.filter(
            feeder=feeder,
            occurred_at=occurred_at,
            interruption_type=fault_type
        ).first()
        
        if existing:
            # Update if we have a restoration time and the existing doesn't
            if restored_at and not existing.restored_at:
                if not dry_run:
                    existing.restored_at = restored_at
                    existing.save()
                stats['interruptions_updated'] += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Updated: {feeder.name} - {fault_type} at {occurred_at} "
                        f"(restored at {restored_at})"
                    )
                )
            else:
                stats['interruptions_skipped'] += 1
        else:
            # Create new interruption
            if not dry_run:
                FeederInterruption.objects.create(
                    feeder=feeder,
                    interruption_type=fault_type,
                    occurred_at=occurred_at,
                    restored_at=restored_at,
                    description=f"Imported from external database"
                )
            stats['interruptions_created'] += 1
            
            status = f"(restored at {restored_at})" if restored_at else "(ongoing)"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created: {feeder.name} - {fault_type} at {occurred_at} {status}"
                )
            )