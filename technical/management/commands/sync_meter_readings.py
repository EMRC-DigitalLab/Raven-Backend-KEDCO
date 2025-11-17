# technical/management/commands/sync_meter_readings.py
# ALTERNATIVE VERSION USING PyMySQL (if mysqlclient installation fails)

import pymysql
pymysql.install_as_MySQLdb()  # This makes pymysql work as MySQLdb

from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from datetime import datetime
from common.models import Feeder
from technical.models import CumulativeMeterReading


class Command(BaseCommand):
    help = 'Sync cumulative meter readings from external MySQL database to CumulativeMeterReading model'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-date',
            type=str,
            help='Start date for fetching data (YYYY-MM-DD format)',
        )
        parser.add_argument(
            '--end-date',
            type=str,
            help='End date for fetching data (YYYY-MM-DD format)',
        )
        parser.add_argument(
            '--feeder-slug',
            type=str,
            help='Specific feeder slug to sync (optional)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without making any changes to the database',
        )
        parser.add_argument(
            '--update-existing',
            action='store_true',
            help='Update existing records if they already exist',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of records to process in each batch (default: 100)',
        )

    def handle(self, *args, **options):
        start_date = options.get('start_date')
        end_date = options.get('end_date')
        feeder_slug = options.get('feeder_slug')
        dry_run = options.get('dry_run')
        update_existing = options.get('update_existing')
        batch_size = options.get('batch_size')

        # Database connection parameters
        db_config = {
            'host': '31.97.56.29',
            'user': 'root',
            'password': 'EMRC-Password-123#',  # Note: 'password' not 'passwd' for pymysql
            'database': 'dataNestDB_KEDCO',    # Note: 'database' not 'db' for pymysql
            'port': 3306,
            'charset': 'utf8mb4',
        }

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be saved'))

        try:
            # Connect to external MySQL database
            self.stdout.write('Connecting to external database...')
            connection = pymysql.connect(**db_config)
            cursor = connection.cursor()

            # Build query
            query = """
                SELECT 
                    energy_id,
                    Feeder_id,
                    Date,
                    Energy_Reading,
                    User_id,
                    Submitted_at
                FROM techicalenergyreadingdailydta
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
                query += " AND Feeder_id = %s"
                params.append(feeder_slug)

            query += " ORDER BY Date ASC, Feeder_id ASC"

            # Execute query
            self.stdout.write(f'Fetching data from external database...')
            cursor.execute(query, params)
            rows = cursor.fetchall()

            self.stdout.write(self.style.SUCCESS(f'Found {len(rows)} records to process'))

            # Statistics
            stats = {
                'total': len(rows),
                'created': 0,
                'updated': 0,
                'skipped': 0,
                'errors': 0,
            }

            # Process records in batches
            batch = []
            for idx, row in enumerate(rows, 1):
                energy_id, feeder_id, date, energy_reading, user_id, submitted_at = row

                try:
                    # Validate and convert data
                    if not feeder_id:
                        self.stdout.write(
                            self.style.WARNING(f'Skipping record {energy_id}: Missing Feeder_id')
                        )
                        stats['skipped'] += 1
                        continue

                    if not date:
                        self.stdout.write(
                            self.style.WARNING(f'Skipping record {energy_id}: Missing Date')
                        )
                        stats['skipped'] += 1
                        continue

                    if energy_reading is None:
                        self.stdout.write(
                            self.style.WARNING(f'Skipping record {energy_id}: Missing Energy_Reading')
                        )
                        stats['skipped'] += 1
                        continue

                    # Get feeder by slug
                    try:
                        feeder = Feeder.objects.get(slug=feeder_id)
                    except Feeder.DoesNotExist:
                        self.stdout.write(
                            self.style.WARNING(
                                f'Skipping record {energy_id}: Feeder with slug "{feeder_id}" not found'
                            )
                        )
                        stats['skipped'] += 1
                        continue

                    # Convert energy reading to Decimal (assuming it's in MWh)
                    # If the external DB stores in kWh, divide by 1000
                    cumulative_mwh = Decimal(str(energy_reading))

                    # Parse submitted_at timestamp
                    reading_time = None
                    if submitted_at:
                        if isinstance(submitted_at, datetime):
                            reading_time = submitted_at
                        else:
                            try:
                                reading_time = datetime.strptime(
                                    str(submitted_at), '%Y-%m-%d %H:%M:%S'
                                )
                            except ValueError:
                                pass

                    # Make reading_time timezone-aware if needed
                    if reading_time and timezone.is_naive(reading_time):
                        reading_time = timezone.make_aware(reading_time)

                    # Prepare data
                    reading_data = {
                        'feeder': feeder,
                        'reading_date': date,
                        'cumulative_mwh': cumulative_mwh,
                        'reading_time': reading_time,
                        'is_estimated': False,
                        'notes': f'Synced from external DB (energy_id: {energy_id}, user_id: {user_id})',
                    }

                    batch.append(reading_data)

                    # Process batch when it reaches batch_size or it's the last record
                    if len(batch) >= batch_size or idx == len(rows):
                        if not dry_run:
                            self._process_batch(batch, update_existing, stats)
                        else:
                            self._process_batch_dry_run(batch, update_existing, stats)
                        
                        # Show progress
                        self.stdout.write(f'Processed {idx}/{len(rows)} records...')
                        batch = []

                except Exception as e:
                    stats['errors'] += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'Error processing record {energy_id}: {str(e)}'
                        )
                    )

            # Close database connection
            cursor.close()
            connection.close()

            # Print summary
            self.stdout.write('\n' + '=' * 60)
            self.stdout.write(self.style.SUCCESS('SYNC SUMMARY'))
            self.stdout.write('=' * 60)
            self.stdout.write(f'Total records processed: {stats["total"]}')
            self.stdout.write(self.style.SUCCESS(f'Created: {stats["created"]}'))
            self.stdout.write(f'Updated: {stats["updated"]}')
            self.stdout.write(f'Skipped: {stats["skipped"]}')
            if stats['errors'] > 0:
                self.stdout.write(self.style.ERROR(f'Errors: {stats["errors"]}'))
            self.stdout.write('=' * 60)

            if dry_run:
                self.stdout.write(
                    self.style.WARNING('\nDRY RUN completed - no changes were saved')
                )

        except pymysql.Error as e:
            raise CommandError(f'Database error: {str(e)}')
        except Exception as e:
            raise CommandError(f'Unexpected error: {str(e)}')

    def _process_batch(self, batch, update_existing, stats):
        """Process a batch of records"""
        for reading_data in batch:
            try:
                with transaction.atomic():
                    feeder = reading_data.pop('feeder')
                    reading_date = reading_data.pop('reading_date')
                    
                    if update_existing:
                        obj, created = CumulativeMeterReading.objects.update_or_create(
                            feeder=feeder,
                            reading_date=reading_date,
                            defaults=reading_data
                        )
                        if created:
                            stats['created'] += 1
                        else:
                            stats['updated'] += 1
                    else:
                        obj, created = CumulativeMeterReading.objects.get_or_create(
                            feeder=feeder,
                            reading_date=reading_date,
                            defaults=reading_data
                        )
                        if created:
                            stats['created'] += 1
                        else:
                            stats['skipped'] += 1
            except Exception as e:
                stats['errors'] += 1
                self.stdout.write(
                    self.style.ERROR(f'Error saving record: {str(e)}')
                )

    def _process_batch_dry_run(self, batch, update_existing, stats):
        """Process a batch in dry-run mode"""
        for reading_data in batch:
            feeder = reading_data['feeder']
            reading_date = reading_data['reading_date']
            
            exists = CumulativeMeterReading.objects.filter(
                feeder=feeder,
                reading_date=reading_date
            ).exists()

            if exists and update_existing:
                stats['updated'] += 1
            elif exists:
                stats['skipped'] += 1
            else:
                stats['created'] += 1