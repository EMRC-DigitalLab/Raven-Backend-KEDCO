import re
import time as time_module
from collections import defaultdict
from datetime import date, datetime, time, timedelta

import pymysql  # type: ignore
from decouple import config
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from django.utils.timezone import make_aware
from tqdm import tqdm  # type: ignore

from common.models import Band, BusinessDistrict, Feeder, InjectionSubstation, State
from financial.models import GLBreakdown, Opex, OpexCategory
from hr.models import Department, Role, Staff
from technical.models import FeederInterruption, HourlyLoad


def parse_nullable(value, fallback=None):
    return value if value not in [None, '', 'NULL'] else fallback


class Command(BaseCommand):
    help = 'Import hourly data and detect faults from external MySQL database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--chunk-size',
            type=int,
            default=50000,
            help='Number of records to process in each chunk (default: 50000)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of records to insert in each batch (default: 1000)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without actually inserting data (for testing)'
        )

    def handle(self, *args, **options):
        chunk_size = options['chunk_size']
        batch_size = options['batch_size']
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be inserted"))

        self.stdout.write(self.style.MIGRATE_HEADING("Connecting to external MySQL database..."))

        conn = pymysql.connect(
            host=config("legacy_mysql_server"),
            user=config("legacy_user"),
            password=config("legacy_password"),
            db=config("legacy_db"),
            cursorclass=pymysql.cursors.DictCursor
        )

        try:
            self.import_hourly_data_with_faults(conn, chunk_size, batch_size, dry_run)
        finally:
            conn.close()
            self.stdout.write(self.style.SUCCESS("Database connection closed."))

    def import_hourly_data_with_faults(self, conn, chunk_size, batch_size, dry_run):
        self.stdout.write(self.style.HTTP_INFO("\nImporting Hourly Data and Detecting Faults..."))

        count = 0
        fault_count = 0
        skipped = 0

        # Fault code mapping — built dynamically from FaultTypeCategory admin records.
        # Every code in the admin maps to itself (identity).
        # The three entries below handle raw DataNest quirks where the source
        # sends a different string than the standardised code we store.
        from technical.models import FaultTypeCategory
        FAULT_CODE_MAP = {obj.code: obj.code for obj in FaultTypeCategory.objects.all()}
        FAULT_CODE_MAP.update({
            "ON":  "O/N",       # DataNest sends "ON",  we store "O/N"
            "L/F": "132KV L/F", # DataNest sends "L/F", we store "132KV L/F"
            "ls":  "L/S",       # DataNest sends "ls",  we store "L/S"
        })

        def reconnect_if_needed(conn):
            """Reconnect to database if connection is lost"""
            try:
                conn.ping(reconnect=True)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Reconnecting to database: {e}"))
                # Force reconnection
                conn.close()
                conn.connect()

        def get_total_count(conn):
            """Get total count of records for progress tracking"""
            try:
                reconnect_if_needed(conn)
                with conn.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) as count FROM Technicalhourlydata")
                    result = cursor.fetchone()
                    return result['count'] if result else 0
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Could not get total count: {e}"))
                return 0

        def is_numeric_load(value):
            """Check if a value represents a numeric load"""
            if value is None:
                return False
            try:
                float(str(value).strip())
                return True
            except (ValueError, TypeError):
                return False

        def parse_hour_to_datetime(date_str, hour_str):
            """Convert date and hour to datetime object"""
            try:
                # Parse the date
                if isinstance(date_str, str):
                    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                else:
                    date_obj = date_str
                
                # Parse hour (format: "01:00:00.000000" or similar)
                if isinstance(hour_str, str):
                    # Extract just the hour part
                    hour_match = re.match(r'(\d{1,2})', hour_str)
                    if hour_match:
                        hour = int(hour_match.group(1))
                    else:
                        hour = 0
                else:
                    # If it's already a time object
                    hour = hour_str.hour if hasattr(hour_str, 'hour') else 0
                
                # Create datetime
                dt = datetime.combine(date_obj, datetime.min.time().replace(hour=hour))
                return make_aware(dt) if dt.tzinfo is None else dt
            except Exception as e:
                return None

        # Pre-load all feeders
        self.stdout.write(self.style.HTTP_INFO("Pre-loading feeders..."))
        feeder_lookup = {feeder.slug: feeder for feeder in Feeder.objects.all()}
        self.stdout.write(self.style.HTTP_INFO(f"Loaded {len(feeder_lookup)} feeders"))

        # Pre-load existing interruptions to avoid duplicates
        self.stdout.write(self.style.HTTP_INFO("Pre-loading existing interruptions..."))
        existing_interruptions = set()
        for interruption in FeederInterruption.objects.select_related('feeder').values(
            'feeder__slug', 'occurred_at', 'interruption_type'
        ):
            key = (
                interruption['feeder__slug'],
                interruption['occurred_at'],
                interruption['interruption_type']
            )
            existing_interruptions.add(key)

        # Get total count for progress tracking
        total_records = get_total_count(conn)
        self.stdout.write(self.style.HTTP_INFO(f"Total records to process: {total_records:,}"))

        # Process data in chunks to avoid connection timeout
        offset = 0
        new_interruptions = []
        processed_keys = set()
        
        # Track active faults across chunks (global state)
        global_active_faults = defaultdict(dict)  # feeder_slug -> {fault_type -> occurred_at}

        while True:
            try:
                reconnect_if_needed(conn)
                
                with conn.cursor() as cursor:
                    # Configure cursor timeout
                    cursor.execute("SET SESSION interactive_timeout = 300")
                    cursor.execute("SET SESSION wait_timeout = 300")
                    
                    # Fetch chunk with LIMIT and OFFSET
                    cursor.execute(f"""
                        SELECT feeder_id, Date, Hour_d, LoadS, Hour
                        FROM Technicalhourlydata
                        ORDER BY feeder_id, Date, Hour_d
                        LIMIT {chunk_size} OFFSET {offset}
                    """)

                    rows = cursor.fetchall()
                    
                    if not rows:
                        break  # No more data

                    self.stdout.write(self.style.HTTP_INFO(
                        f"Processing chunk {offset//chunk_size + 1}: "
                        f"records {offset:,} - {offset + len(rows):,}"
                    ))

                    # Group current chunk by feeder
                    chunk_feeder_data = defaultdict(list)
                    for row in rows:
                        feeder_slug = row["feeder_id"].strip()
                        chunk_feeder_data[feeder_slug].append(row)

                    # Process each feeder's data in this chunk
                    for feeder_slug, feeder_rows in tqdm(
                        chunk_feeder_data.items(), 
                        desc=f"Processing Chunk {offset//chunk_size + 1}", 
                        unit="feeder"
                    ):
                        feeder = feeder_lookup.get(feeder_slug)
                        if not feeder:
                            skipped += len(feeder_rows)
                            continue

                        # Get active faults for this feeder from global state
                        active_faults = global_active_faults[feeder_slug]
                        
                        for row in feeder_rows:
                            load_value = str(row["LoadS"]).strip() if row["LoadS"] else ""
                            datetime_obj = parse_hour_to_datetime(row["Date"], row["Hour"])
                            
                            if not datetime_obj:
                                skipped += 1
                                continue

                            count += 1

                            # Check if this is a fault code
                            if load_value in FAULT_CODE_MAP:
                                fault_type = FAULT_CODE_MAP[load_value]
                                
                                # Only create new fault if not already active
                                if fault_type not in active_faults:
                                    unique_key = (feeder_slug, datetime_obj, fault_type)
                                    
                                    if unique_key not in existing_interruptions and unique_key not in processed_keys:
                                        new_interruptions.append(
                                            FeederInterruption(
                                                feeder=feeder,
                                                occurred_at=datetime_obj,
                                                restored_at=None,
                                                interruption_type=fault_type,
                                                description=f"Detected from hourly data - Code: {load_value}",
                                            )
                                        )
                                        processed_keys.add(unique_key)
                                        active_faults[fault_type] = datetime_obj
                                        fault_count += 1

                            elif is_numeric_load(load_value):
                                # Numeric load found - close all active faults for this feeder
                                for fault_type, occurred_at in list(active_faults.items()):
                                    # Find corresponding interruption in current batch
                                    for interruption in new_interruptions:
                                        if (interruption.feeder == feeder and 
                                            interruption.occurred_at == occurred_at and 
                                            interruption.interruption_type == fault_type and
                                            interruption.restored_at is None):
                                            
                                            interruption.restored_at = datetime_obj
                                            break
                                    
                                    # Remove from active faults
                                    del active_faults[fault_type]

                            # Batch insert when we reach batch_size
                            if len(new_interruptions) >= batch_size:
                                if not dry_run:
                                    try:
                                        FeederInterruption.objects.bulk_create(
                                            new_interruptions, 
                                            batch_size=batch_size,
                                            ignore_conflicts=True
                                        )
                                    except Exception as e:
                                        self.stdout.write(self.style.ERROR(f"Error inserting batch: {e}"))
                                else:
                                    self.stdout.write(self.style.HTTP_INFO(f"DRY RUN: Would insert {len(new_interruptions)} records"))
                                
                                new_interruptions = []

                    offset += len(rows)
                    
                    # Progress update
                    progress_pct = (offset / total_records * 100) if total_records > 0 else 0
                    self.stdout.write(self.style.HTTP_INFO(
                        f"Progress: {progress_pct:.1f}% ({offset:,}/{total_records:,})"
                    ))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error processing chunk at offset {offset}: {e}"))
                # Try to reconnect and continue
                try:
                    reconnect_if_needed(conn)
                    time_module.sleep(2)  # Brief pause before retry
                except:
                    self.stdout.write(self.style.ERROR("Could not reconnect. Stopping import."))
                    break

        # Insert remaining records
        if new_interruptions:
            if not dry_run:
                try:
                    FeederInterruption.objects.bulk_create(
                        new_interruptions, 
                        batch_size=batch_size,
                        ignore_conflicts=True
                    )
                    self.stdout.write(self.style.HTTP_INFO(f"Inserted final batch of {len(new_interruptions)} fault records"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error inserting final batch: {e}"))
            else:
                self.stdout.write(self.style.HTTP_INFO(f"DRY RUN: Would insert final batch of {len(new_interruptions)} records"))

        self.stdout.write(self.style.SUCCESS(f"\nHourly data processed: {count:,} records."))
        self.stdout.write(self.style.SUCCESS(f"Fault interruptions {'would be ' if dry_run else ''}created: {fault_count:,} entries."))
        self.stdout.write(self.style.WARNING(f"Skipped: {skipped:,} rows (e.g., missing feeder or invalid data)"))