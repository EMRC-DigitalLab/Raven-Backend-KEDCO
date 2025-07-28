from django.core.management.base import BaseCommand
from common.models import State, BusinessDistrict, InjectionSubstation, Feeder, Band
from financial.models import Opex, OpexCategory, GLBreakdown
from technical.models import HourlyLoad, FeederInterruption
from hr.models import Staff, Department, Role
from django.utils.dateparse import parse_date
from decouple import config
from datetime import date, timedelta
from django.utils.text import slugify
from django.utils.timezone import make_aware
from django.conf import settings
from datetime import datetime, time
import pymysql # type: ignore
from tqdm import tqdm # type: ignore


def parse_nullable(value, fallback=None):
    return value if value not in [None, '', 'NULL'] else fallback


class Command(BaseCommand):
    help = 'Import legacy data from external MySQL database'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.MIGRATE_HEADING("Connecting to external MySQL database..."))

        conn = pymysql.connect(
            host=config("legacy_mysql_server"),
            user=config("legacy_user"),
            password=config("legacy_password"),
            db=config("legacy_db"),
            cursorclass=pymysql.cursors.DictCursor
        )

        with conn:
            # self.import_states(conn)
            # self.import_districts(conn)
            # self.import_injection_stations(conn)
            # self.import_feeders(conn)
            self.import_feeder_interruptions(conn)
            # self.import_expenses_with_breakdowns(conn)
            # self.import_hourly_load(conn)
            # self.import_staff(conn)
            # self.import_distribution_transformers(conn)
            # self.import_sales_reps(conn)
            # self.import_daily_collections(conn)
            # self.import_energy_delivered(conn)
            # self.import_monthly_commercial_summary(conn)


        self.stdout.write(self.style.SUCCESS('Legacy data imported successfully.'))

    def import_states(self, conn):
        self.stdout.write(self.style.HTTP_INFO("\nImporting States..."))
        count = 0
        with conn.cursor() as cursor:
            cursor.execute("SELECT state_id, state_name FROM states")
            for row in cursor.fetchall():
                name = row["state_name"].strip()
                slug = row["state_id"].strip()  # Use ID as slug
                _, created = State.objects.get_or_create(
                    slug=slug,
                    defaults={"name": name}
                )
                count += int(created)
        self.stdout.write(self.style.SUCCESS(f"States imported: {count} new entries."))


    def import_districts(self, conn):
        self.stdout.write(self.style.HTTP_INFO("\nImporting Business Districts..."))
        count = 0
        with conn.cursor() as cursor:
            cursor.execute("SELECT district_id, district_name, state_id FROM business_districts")
            for row in cursor.fetchall():
                name = row["district_name"].strip()
                slug = row["district_id"].strip()
                state = State.objects.filter(slug=row["state_id"].strip()).first()
                if state:
                    _, created = BusinessDistrict.objects.get_or_create(
                        slug=slug,
                        defaults={"name": name, "state": state}
                    )
                    count += int(created)
        self.stdout.write(self.style.SUCCESS(f"Business Districts imported: {count} new entries."))


    def import_injection_stations(self, conn):
        self.stdout.write(self.style.HTTP_INFO("\nImporting Injection Substations..."))
        count = 0
        imported_names = set()

        with conn.cursor() as cursor:
            cursor.execute("SELECT injection_station_id, injection_station_name FROM injection_stations")
            for row in cursor.fetchall():
                name = row["injection_station_name"].strip()
                slug = row["injection_station_id"].strip()

                # Skip if we've already imported a substation with this name
                # if name.lower() in imported_names:
                #     continue

                _, created = InjectionSubstation.objects.get_or_create(
                    slug=slug,
                    defaults={"name": name}
                )

                if created:
                    imported_names.add(name.lower())
                    count += 1

        self.stdout.write(self.style.SUCCESS(f"Substations imported: {count} new entries."))





    def import_feeders(self, conn):
        self.stdout.write(self.style.HTTP_INFO("\nImporting Feeders..."))
        count = 0

        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT feeder_id, feeder_name, injection_station_id, district_id, feeder_type, service_band
                FROM feeders
            """)

            for row in cursor.fetchall():
                name = row["feeder_name"].strip()
                slug = row["feeder_id"].strip()
                feeder_type = (row.get("feeder_type") or "").strip().upper()
                voltage_level = "11kv" if "11" in feeder_type else "33kv"

                substation = InjectionSubstation.objects.filter(slug=row["injection_station_id"].strip()).first()
                district = BusinessDistrict.objects.filter(slug=row["district_id"].strip()).first()

                # Handle band: skip if NULL or 'NAN'
                raw_band = row.get("service_band")
                band_name = (raw_band or "").strip().upper()
                band = None
                if band_name and band_name != "NAN":
                    band = Band.objects.filter(name=band_name).first()
                    if not band:
                        band = Band.objects.create(name=band_name)

                if substation and district:
                    _, created = Feeder.objects.get_or_create(
                        slug=slug,
                        defaults={
                            "name": name,
                            "substation": substation,
                            "voltage_level": voltage_level,
                            "business_district": district,
                            "band": band,  # Will be None if not found or invalid
                        }
                    )
                    count += int(created)

        self.stdout.write(self.style.SUCCESS(f"Feeders imported: {count} new entries."))




    def import_expenses_with_breakdowns(self, conn):
        self.stdout.write(self.style.HTTP_INFO("\nImporting GL Breakdowns and Expenses..."))

        expense_count = 0
        breakdown_created = 0
        skipped_rows = []

        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT district_id, date, purpose_of_transaction, payment_to, 
                       debit, credit, gl_account_code_number, 
                       gl_account_opex_break_down, opex_categorization
                FROM financialopex
            """)

            rows = cursor.fetchall()

            for index, row in enumerate(tqdm(rows, desc="Processing Expenses", unit="row")):
                district_slug = (row.get("district_id") or "").strip()
                breakdown_name = (row.get("gl_account_opex_break_down") or "").strip()
                category_name = (row.get("opex_categorization") or "").strip()

                # Validate district
                district = BusinessDistrict.objects.filter(slug=district_slug).first()
                if not district:
                    skipped_rows.append({
                        "reason": "Unknown district",
                        "district_id": district_slug,
                        "row_index": index
                    })
                    continue

                # Validate breakdown
                if not breakdown_name:
                    skipped_rows.append({
                        "reason": "Missing GL Breakdown",
                        "district_id": district_slug,
                        "row_index": index
                    })
                    continue

                # Get or create breakdown
                breakdown, was_created = GLBreakdown.objects.get_or_create(name=breakdown_name)
                if was_created:
                    breakdown_created += 1

                # Get or create category
                category, _ = OpexCategory.objects.get_or_create(name=category_name or "Uncategorized")

                # Create Expense
                Opex.objects.create(
                    district=district,
                    date=parse_date(str(row["date"])),
                    purpose=parse_nullable(row.get("purpose_of_transaction"), "N/A").strip(),
                    payee=parse_nullable(row.get("payment_to"), "N/A").strip(),
                    debit=parse_nullable(row.get("debit"), 0.0),
                    credit=parse_nullable(row.get("credit"), 0.0),
                    gl_account_number=str(row.get("gl_account_code_number") or "").strip(),
                    gl_breakdown=breakdown,
                    opex_category=category,
                )
                expense_count += 1

        # Final summaries
        self.stdout.write(self.style.SUCCESS(f"\nExpenses imported: {expense_count}"))
        self.stdout.write(self.style.SUCCESS(f"GL Breakdowns created: {breakdown_created}"))
        self.stdout.write(self.style.WARNING(f"Skipped: {len(skipped_rows)} rows"))

        if skipped_rows:
            self.stdout.write(self.style.NOTICE("\nSkipped row log:"))
            for item in skipped_rows:
                self.stdout.write(f"  - Row {item['row_index']}: {item['reason']} (district_id={item['district_id']})")



    def import_hourly_load(self, conn):
        from collections import defaultdict
        from django.db import transaction

        self.stdout.write(self.style.HTTP_INFO("\nImporting Hourly Load Data (optimized)..."))

        count = 0
        skipped_rows = []
        interruptions_created = 0
        batch_size = 1000
        load_batch = []
        interruption_batch = []

        # Preload all feeders to avoid repeated queries
        feeder_map = {f.slug: f for f in Feeder.objects.all()}

        with conn.cursor() as cursor:
            cursor.execute("SELECT MIN(Date) AS min_date, MAX(Date) AS max_date FROM Technicalhourlydata")
            result = cursor.fetchone()
            start_date = result["min_date"]
            end_date = result["max_date"]

            if not start_date or not end_date:
                self.stdout.write(self.style.WARNING("No data found in Technicalhourlydata table."))
                return
            
            self.stdout.write(self.style.HTTP_INFO(f"  → Date range: {start_date} to {end_date}"))

            current = start_date
            while current <= end_date:
                self.stdout.write(self.style.HTTP_INFO(f"→ Importing for {current}"))

                cursor.execute("""
                    SELECT feeder_id, Date, Hour_d, `LoadS`
                    FROM Technicalhourlydata
                    WHERE DATE(Date) = %s
                """, (current,))
                rows = cursor.fetchall()

                self.stdout.write(self.style.HTTP_INFO(f"  → Rows returned: {len(rows)}"))

                for i, row in enumerate(tqdm(rows, desc=f"  Processing {current}", unit="rows")):
                    feeder_slug = (row.get("feeder_id") or "").strip()
                    feeder = feeder_map.get(feeder_slug)

                    if not feeder:
                        skipped_rows.append({"reason": "Unknown feeder", "feeder_id": feeder_slug})
                        continue

                    parsed_date = parse_date(str(row["Date"]))
                    reading_hour = row["Hour_d"]

                    if parsed_date is None or reading_hour is None:
                        skipped_rows.append({"reason": "Invalid date/hour", "feeder_id": feeder_slug})
                        continue

                    load_raw = row["LoadS"]
                    load_str = str(load_raw).strip() if load_raw is not None else ""

                    try:
                        load_value = float(load_str)
                        load_flag = None
                    except ValueError:
                        load_value = None
                        load_flag = load_str.upper()

                    if load_value is not None:
                        load_batch.append(HourlyLoad(
                            feeder=feeder,
                            date=parsed_date,
                            hour=reading_hour,
                            load_mw=load_value
                        ))
                        count += 1
                    elif load_flag:
                        occurred_at = datetime.combine(parsed_date, time(hour=reading_hour))
                        if settings.USE_TZ:
                            occurred_at = make_aware(occurred_at)

                        interruption_batch.append(FeederInterruption(
                            feeder=feeder,
                            occurred_at=occurred_at,
                            interruption_type=load_flag,
                            description="Logged from hourly load record"
                        ))
                        interruptions_created += 1
                    else:
                        skipped_rows.append({"reason": "Empty or invalid load", "feeder_id": feeder_slug})

                    # Flush batch
                    if len(load_batch) >= batch_size:
                        HourlyLoad.objects.bulk_create(load_batch, ignore_conflicts=True)
                        load_batch.clear()

                # After each day, flush
                if load_batch:
                    HourlyLoad.objects.bulk_create(load_batch, ignore_conflicts=True)
                    load_batch.clear()

                if interruption_batch:
                    FeederInterruption.objects.bulk_create(interruption_batch, ignore_conflicts=True)
                    interruption_batch.clear()

                current += timedelta(days=1)

        # Final logs
        self.stdout.write(self.style.SUCCESS(f"\nHourly loads imported: {count}"))
        self.stdout.write(self.style.SUCCESS(f"Interruption events created: {interruptions_created}"))
        self.stdout.write(self.style.WARNING(f"Skipped rows: {len(skipped_rows)}"))
        
        if skipped_rows:
            self.stdout.write(self.style.NOTICE("\nSkipped row log (first 10 shown):"))
            for row in skipped_rows[:10]:
                self.stdout.write(f"  - {row['reason']} for feeder {row['feeder_id']}")


    def import_staff(self, conn):
        from tqdm import tqdm # type: ignore
        self.stdout.write(self.style.HTTP_INFO("\nImporting HR Staff..."))

        count = 0
        previous_hire_date = None
        previous_birth_date = None

        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT staff_id, district_id, name, email, phone_number, gender, salary, 
                       start_date, end_date, Department, grade, age
                FROM hr_staff
            """)
            rows = cursor.fetchall()

            for idx, row in enumerate(tqdm(rows, desc="Processing Staff Records", unit="staff")):
                try:
                    district_slug = (row["district_id"] or "").strip()
                    district = BusinessDistrict.objects.filter(slug=district_slug).first()
                    if not district:
                        self.stdout.write(self.style.WARNING(
                            f"Row {idx}: Skipping — unknown district '{district_slug}'"
                        ))
                        continue

                    dept_name = (row["Department"] or "").strip()
                    grade_name = (row["grade"] or "").strip()

                    # Create or get Department
                    dept_slug = slugify(dept_name)
                    department, _ = Department.objects.get_or_create(
                        slug=dept_slug,
                        defaults={"name": dept_name}
                    )

                    # Create or get Role
                    role_slug = slugify(grade_name)
                    role, _ = Role.objects.get_or_create(
                        slug=role_slug,
                        defaults={"title": grade_name, "department": department}
                    )

                    raw_hire_date = row.get("start_date")
                    hire_date = parse_date(str(raw_hire_date)) if raw_hire_date else previous_hire_date
                    if hire_date:
                        previous_hire_date = hire_date
                    else:
                        self.stdout.write(self.style.WARNING(
                            f"Row {idx}: Skipping staff '{row['name'].strip()}' — no valid hire date."
                        ))
                        continue

                    raw_age = row.get("age")
                    if raw_age and isinstance(raw_age, (int, float)):
                        birth_date = date.today() - timedelta(days=round(float(raw_age) * 365.25))
                        previous_birth_date = birth_date
                    elif previous_birth_date:
                        birth_date = previous_birth_date
                    else:
                        birth_date = date.today() - timedelta(days=30 * 365)

                    Staff.objects.create(
                        full_name=row["name"].strip(),
                        email=parse_nullable(row.get("email")),
                        phone_number=parse_nullable(row.get("phone_number"), "N/A"),
                        gender=row.get("gender"),
                        birth_date=birth_date,
                        salary=parse_nullable(row.get("salary"), 0),
                        hire_date=hire_date,
                        exit_date=parse_date(str(row.get("end_date"))) if row.get("end_date") else None,
                        grade=grade_name,
                        role=role,
                        department=department,
                        district=district,
                        state=district.state if district else None
                    )
                    count += 1

                except Exception as e:
                    self.stdout.write(self.style.ERROR(
                        f"Row {idx}: Failed to import staff '{row.get('name', '[No Name]')}'. Error: {e}"
                    ))

        self.stdout.write(self.style.SUCCESS(f"\nStaff imported: {count} entries."))




    def import_feeder_interruptions(self, conn):
        self.stdout.write(self.style.HTTP_INFO("\nImporting Feeder Interruptions..."))

        count = 0
        skipped = 0
        batch_size = 1000

        # Comprehensive fault type mapping
        FAULT_TYPE_MAP = {
        # Original mappings
        "Earth Fault": "E/F",
        "Overcurrent Fault": "O/C",
        "Overcurrent & Earth Fault": "O/C & E/F",
        "E/F": "E/F",
        "O/C": "O/C",
        "O/C & E/F": "O/C & E/F",
        
        # Extended mappings for new types
        "No RI": "NO RI",
        "Load Shedding": "L/S",
        "Load Shedding (L/S)": "L/S",
        "Overload": "O/S",
        "Overcurrent (O/S)": "O/S",
        "Transformer Fault": "T/F",
        "Transformer Fault (T/F)": "T/F",
        "Bus Fault": "B/F",
        "Breakdown Fault": "B/F",
        "Bus/Breakdown Fault (B/F)": "B/F",
        "Overheating": "O/N",
        "Overtemp": "O/N",
        "Overheating/Overtemp (O/N)": "O/N",
        "Open Earth": "O/E",
        "Open Earth (O/E)": "O/E",
        "Phase Open": "P/O",
        "Phase Open (P/O)": "P/O",
        "Over Frequency": "O/F",
        "Over Frequency (O/F)": "O/F",
        "Phase Missing": "P/M",
        "Phase Metering": "P/M",
        "Phase Missing/Phase Metering (P/M)": "P/M",
        "Other Faults": "O",
        "Operational Fault": "O",
        "Other Faults/Operational Fault (O)": "O",
        "Trip Fault": "T/S",
        "Surge Fault": "T/S",
        "Trip/Surge Fault (T/S)": "T/S",
        "Load Shedding – General Supply": "L/S GS",
        "Load Shedding General Supply": "L/S GS",
        "Maintenance": "MTNC",
        "Maintenance (MTNC)": "MTNC",
        "MTCE": "MTCE",
        "Open Circuit & Earth Fault": "OC & E/F",
        "OC & E/F": "OC & E/F",
        "Emergency": "EM/D",
        "Device": "EM/D",
        "Emergency/Device (EM/D)": "EM/D",
        "330 kV Line Fault": "330KV L/F",
        "330KV L/F": "330KV L/F",
        "Switch Off": "OFF",
        "Feeder Off": "OFF",
        "Switch Off/Feeder Off (OFF)": "OFF",
        "Short Circuit": "S/C",
        "Short Circuit (S/C)": "S/C",
        "132 kV Earth Fault": "132KV E/F",
        "132KV E/F": "132KV E/F",
        "132 kV Line Fault": "132KV L/F",
        "132KV L/F": "132KV L/F",
        "330 kV Line Shelving": "330KV L/S",
        "330 kV Load Shedding": "330KV L/S",
        "330KV L/S": "330KV L/S",
        "132 kV Circuit Breaker Failure": "132KV CB/F",
        "132KV CB/F": "132KV CB/F",
        "Double Circuit": "D/C",
        "Direct Current": "D/C",
        "Double Circuit/Direct Current (D/C)": "D/C",
        "Incoming Over Current": "IN O/C",
        "Infeeder OC": "IN O/C",
        "Incoming Over Current/Infeeder OC (IN O/C)": "IN O/C",
        "Thermal Load Shedding": "T/LS",
        "Thermal Load Shedding (T/LS)": "T/LS",
        "132 kV Maintenance": "132KV MTCE",
        "132KV MTCE": "132KV MTCE",
        "Lightning Impulse": "LIM",
        "Limit": "LIM",
        "Lightning Impulse/Limit (LIM)": "LIM",
        "tcn": "tcn",
        "fault": "fault",
        "permit": "permit",
        
        # Default cases
        None: "N/A",
        "": "N/A",
        }

        # Fault types to skip completely (lowercase match)
        SKIP_VALUES = {
            "there is nothing here since the feeder was opened on planned outage( check g43 for detail)"
        }

        # Pre-load all feeders into memory for fast lookup
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
        self.stdout.write(self.style.HTTP_INFO(f"Loaded {len(existing_interruptions)} existing interruptions"))

        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT feeder_id, time_of_occurrence, time_of_restoration,
                       load_interrupted_mw, fault_type, description
                FROM technicalenergyfault
            """)

            rows = cursor.fetchall()
            new_interruptions = []
            processed_keys = set()  # Track what we've already processed in this batch

            for row in tqdm(rows, desc="Processing Fault Records", unit="row"):
                feeder_slug = row["feeder_id"].strip()
            
                # Fast feeder lookup
                feeder = feeder_lookup.get(feeder_slug)
                if not feeder:
                    self.stdout.write(self.style.WARNING(f"Feeder not found: {feeder_slug}"))
                    skipped += 1
                    continue

                raw_fault_type = (row.get("fault_type") or "").strip()
                fault_type_lower = raw_fault_type.lower()

                # Skip invalid fault types
                if fault_type_lower in SKIP_VALUES or len(raw_fault_type) > 100:
                    self.stdout.write(self.style.WARNING(f"Skipped invalid fault type: '{raw_fault_type}'"))
                    skipped += 1
                    continue

                # Map fault type with fuzzy matching for better coverage
                interruption_type = FAULT_TYPE_MAP.get(raw_fault_type)
                if not interruption_type:
                    # Try case-insensitive matching
                    for key, value in FAULT_TYPE_MAP.items():
                        if key and raw_fault_type.lower() == key.lower():
                            interruption_type = value
                            break
                
                    # If still not found, default to "N/A" but truncate
                    if not interruption_type:
                        interruption_type = "N/A"
            
                # Ensure max length
                interruption_type = interruption_type[:100]

                occurred_at = row["time_of_occurrence"]
                restored_at = row.get("time_of_restoration")
                description = parse_nullable(row.get("description"), "").strip()

                # Ensure datetimes are timezone-aware
                if occurred_at and occurred_at.tzinfo is None:
                    occurred_at = make_aware(occurred_at)
                if restored_at and restored_at.tzinfo is None:
                    restored_at = make_aware(restored_at)

                # Create unique key for duplicate checking
                unique_key = (feeder_slug, occurred_at, interruption_type)
            
                # Check if already exists (in DB or current batch)
                if unique_key not in existing_interruptions and unique_key not in processed_keys:
                    new_interruptions.append(
                        FeederInterruption(
                            feeder=feeder,
                            occurred_at=occurred_at,
                            restored_at=restored_at,
                            interruption_type=interruption_type,
                            description=description,
                        )
                    )
                    processed_keys.add(unique_key)
                    count += 1

                    # Batch insert when we reach batch_size
                    if len(new_interruptions) >= batch_size:
                        FeederInterruption.objects.bulk_create(
                            new_interruptions, 
                            batch_size=batch_size,
                            ignore_conflicts=True
                        )
                        self.stdout.write(self.style.HTTP_INFO(f"Inserted batch of {len(new_interruptions)} records"))
                        new_interruptions = []

            # Insert remaining records
            if new_interruptions:
                FeederInterruption.objects.bulk_create(
                    new_interruptions, 
                    batch_size=batch_size,
                    ignore_conflicts=True
                )
                self.stdout.write(self.style.HTTP_INFO(f"Inserted final batch of {len(new_interruptions)} records"))

        self.stdout.write(self.style.SUCCESS(f"\nFeeder interruptions imported: {count} entries."))
        self.stdout.write(self.style.WARNING(f"Skipped: {skipped} rows (e.g., missing feeder or invalid fault type)"))


    def import_distribution_transformers(self, conn):
        from common.models import Feeder, DistributionTransformer
        from tqdm import tqdm # type: ignore

        self.stdout.write(self.style.HTTP_INFO("\nImporting Distribution Transformers..."))

        count = 0
        skipped = 0
        errors = []

        # Preload all feeders into a map {slug: Feeder}
        feeder_map = {f.slug: f for f in Feeder.objects.all()}

        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT dt_id, dt_name, feeder_id
                FROM distribution_channels
            """)
            rows = cursor.fetchall()

            for idx, row in enumerate(tqdm(rows, desc="Processing DTs", unit="dt")):
                try:
                    dt_slug = (row["dt_id"] or "").strip()
                    dt_name = (row["dt_name"] or "").strip()
                    feeder_slug = (row["feeder_id"] or "").strip()

                    if not dt_slug or not dt_name or not feeder_slug:
                        skipped += 1
                        continue

                    feeder = feeder_map.get(feeder_slug)
                    if not feeder:
                        self.stdout.write(self.style.WARNING(
                            f"Row {idx}: Skipped — Feeder not found for slug '{feeder_slug}'"
                        ))
                        skipped += 1
                        continue

                    # Create DT only if it doesn't already exist by slug
                    _, created = DistributionTransformer.objects.get_or_create(
                        slug=dt_slug,
                        defaults={
                            "name": dt_name,
                            "feeder": feeder
                        }
                    )
                    count += int(created)

                except Exception as e:
                    self.stdout.write(self.style.ERROR(
                        f"Row {idx}: Error importing DT '{row.get('dt_name', '[No Name]')}' — {e}"
                    ))
                    skipped += 1
                    errors.append((idx, str(e)))

        self.stdout.write(self.style.SUCCESS(f"\nDistribution Transformers imported: {count}"))
        self.stdout.write(self.style.WARNING(f"Skipped: {skipped} rows"))

        if errors:
            self.stdout.write(self.style.NOTICE("First 5 errors:"))
            for idx, err in errors[:5]:
                self.stdout.write(f"  - Row {idx}: {err}")


    def import_sales_reps(self, conn):
        from commercial.models import SalesRepresentative
        from common.models import DistributionTransformer
        from tqdm import tqdm # type: ignore

        self.stdout.write(self.style.HTTP_INFO("\nImporting Sales Representatives and assigning transformers..."))

        created_count = 0
        linked_count = 0
        skipped_reps = 0
        skipped_links = 0

        with conn.cursor() as cursor:
            # Step 1: Import Sales Representatives from sales_reps table
            self.stdout.write(self.style.HTTP_INFO("Step 1: Creating Sales Representatives..."))
        
            cursor.execute("SELECT sales_rep_id, sales_rep_name FROM sales_reps")
            reps = cursor.fetchall()

            for row in tqdm(reps, desc="Creating Sales Reps", unit="rep"):
                rep_id = parse_nullable(row.get("sales_rep_id"), "").strip()
                rep_name = parse_nullable(row.get("sales_rep_name"), "").strip()

                if not rep_id or not rep_name:
                    self.stdout.write(self.style.WARNING(
                        f"Skipping sales rep with missing ID ({rep_id}) or name ({rep_name})"
                    ))
                    skipped_reps += 1
                    continue

                # Create or get the sales representative
                rep, created = SalesRepresentative.objects.get_or_create(
                    slug=rep_id,  # sales_rep_id becomes the slug
                    defaults={"name": rep_name}
                )
            
                if created:
                    created_count += 1
                else:
                    # Update name if it changed
                    if rep.name != rep_name:
                        rep.name = rep_name
                        rep.save()

            # Step 2: Assign transformers from sales_rep_dt table
            self.stdout.write(self.style.HTTP_INFO("Step 2: Linking Sales Reps to Transformers..."))
        
            cursor.execute("SELECT sales_rep_id, dt_id FROM sales_rep_dt")
            assignments = cursor.fetchall()

            # Clear all existing assignments first (optional - remove if you want to preserve existing)
            # SalesRepresentative.objects.all().update(assigned_transformers=None)

            for row in tqdm(assignments, desc="Linking Sales Reps to Transformers", unit="link"):
                rep_id = parse_nullable(row.get("sales_rep_id"), "").strip()
                dt_id = parse_nullable(row.get("dt_id"), "").strip()

                if not rep_id or not dt_id:
                    self.stdout.write(self.style.WARNING(
                        f"Skipping assignment with missing rep_id ({rep_id}) or dt_id ({dt_id})"
                    ))
                    skipped_links += 1
                    continue

                # Find the sales rep by slug (which is the sales_rep_id)
                try:
                    rep = SalesRepresentative.objects.get(slug=rep_id)
                except SalesRepresentative.DoesNotExist:
                    self.stdout.write(self.style.WARNING(
                        f"Sales rep with ID {rep_id} not found - skipping transformer assignment for {dt_id}"
                    ))
                    skipped_links += 1
                    continue

                # Find the transformer by slug (which is the dt_id)
                try:
                    transformer = DistributionTransformer.objects.get(slug=dt_id)
                except DistributionTransformer.DoesNotExist:
                    self.stdout.write(self.style.WARNING(
                        f"Transformer with ID {dt_id} not found - skipping assignment to rep {rep_id}"
                    ))
                    skipped_links += 1
                    continue

                # Add the transformer to the sales rep's assigned transformers
                rep.assigned_transformers.add(transformer)
                linked_count += 1

        # Summary
        self.stdout.write(self.style.SUCCESS(f"\n{'='*50}"))
        self.stdout.write(self.style.SUCCESS("IMPORT SUMMARY"))
        self.stdout.write(self.style.SUCCESS(f"{'='*50}"))
        self.stdout.write(self.style.SUCCESS(f"Sales Reps created: {created_count}"))
        self.stdout.write(self.style.SUCCESS(f"Transformer assignments created: {linked_count}"))
        if skipped_reps > 0:
            self.stdout.write(self.style.WARNING(f"Skipped sales reps: {skipped_reps} (missing data)"))
        if skipped_links > 0:
            self.stdout.write(self.style.WARNING(f"Skipped assignments: {skipped_links} (missing rep or transformer)"))
        self.stdout.write(self.style.SUCCESS(f"{'='*50}"))



    def import_daily_collections(self, conn):
        from commercial.models import DailyCollection, SalesRepresentative

        self.stdout.write(self.style.HTTP_INFO("\nImporting Daily Collections (by sales rep)..."))
        count = 0
        skipped = 0

        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT sales_rep_id, metric_date, collections
                FROM commercial_metrics
                WHERE collections IS NOT NULL
            """)
            for row in cursor.fetchall():
                rep_slug = parse_nullable(row.get("sales_rep_id"), "").strip()
                date = row.get("metric_date")
                amount = row.get("collections")

                if not rep_slug or not date or amount is None:
                    self.stdout.write(self.style.WARNING(f"Skipped row with missing values."))
                    skipped += 1
                    continue

                rep = SalesRepresentative.objects.filter(slug=rep_slug).first()
                if not rep:
                    self.stdout.write(self.style.WARNING(f"Sales Rep not found: {rep_slug}"))
                    skipped += 1
                    continue

                DailyCollection.objects.create(
                    sales_rep=rep,
                    date=date,
                    amount=amount,
                    collection_type='Postpaid',  # Assumed default
                    vendor_name='N/A'        # Placeholder
                )
                count += 1

        self.stdout.write(self.style.SUCCESS(f"Daily collections imported: {count}"))
        self.stdout.write(self.style.WARNING(f"Skipped entries: {skipped}"))


    def import_energy_delivered(self, conn):
        from technical.models import EnergyDelivered
        from common.models import Feeder
        from tqdm import tqdm #type: ignore

        self.stdout.write(self.style.HTTP_INFO("\nImporting Energy Delivered..."))
        count = 0
        skipped = 0

        with conn.cursor() as cursor:
            # Get total count for progress bar
            cursor.execute("SELECT COUNT(*) as total FROM techicalenergyreadingdailydta")
            total_rows = cursor.fetchone()['total']

            # Fetch all rows
            cursor.execute("""
                SELECT Feeder_id, Date, Energy_Reading
                FROM techicalenergyreadingdailydta
            """)

            for row in tqdm(cursor.fetchall(), total=total_rows, desc="Importing Energy Readings"):
                feeder_slug = parse_nullable(row.get("Feeder_id"), "").strip()
                date = row.get("Date")
                energy_raw = row.get("Energy_Reading")

                feeder = Feeder.objects.filter(slug=feeder_slug).first()
                if not feeder or not date or energy_raw is None:
                    skipped += 1
                    continue

                try:
                    energy_mwh = round(float(energy_raw), 2)
                except (ValueError, TypeError):
                    skipped += 1
                    continue

                EnergyDelivered.objects.update_or_create(
                    feeder=feeder,
                    date=date,
                    defaults={"energy_mwh": energy_mwh}
                )
                count += 1

        self.stdout.write(self.style.SUCCESS(f"Energy Delivered imported: {count}"))
        self.stdout.write(self.style.WARNING(f"Skipped rows: {skipped}"))



    def import_monthly_commercial_summary(self, conn):
        from commercial.models import MonthlyCommercialSummary, SalesRepresentative
        from common.models import DistributionTransformer
        from tqdm import tqdm #type: ignore
        from datetime import datetime

        self.stdout.write(self.style.HTTP_INFO("\nImporting Monthly Commercial Summary..."))
        created_count = 0
        updated_count = 0
        skipped = 0

        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT sales_rep_id, dt_id, month, pop, response, billed, payment
                FROM commercial
            """)
            rows = cursor.fetchall()

            for row in tqdm(rows, desc="Processing Monthly Commercial Summaries", unit="row"):
                sales_rep_id = (row.get("sales_rep_id") or "").strip()
                dt_id = (row.get("dt_id") or "").strip()
            
                # Find sales rep by slug
                sales_rep = SalesRepresentative.objects.filter(slug=sales_rep_id).first()
                if not sales_rep:
                    self.stdout.write(self.style.WARNING(f"Skipping row - Sales Rep not found: {sales_rep_id}"))
                    skipped += 1
                    continue

                # Find transformer by slug
                transformer = DistributionTransformer.objects.filter(slug=dt_id).first()
                if not transformer:
                    self.stdout.write(self.style.WARNING(f"Skipping row - Transformer not found: {dt_id}"))
                    skipped += 1
                    continue

                # Validate that sales rep is assigned to this transformer
                if not sales_rep.assigned_transformers.filter(id=transformer.id).exists():
                    self.stdout.write(self.style.WARNING(
                        f"Skipping row - Sales Rep {sales_rep_id} not assigned to transformer {dt_id}"
                    ))
                    skipped += 1
                    continue

                # Parse month - handle datetime format from source database
                month_value = row.get("month")
                if month_value:
                    if isinstance(month_value, str):
                        # Parse string datetime to date
                        try:
                            month_date = datetime.strptime(month_value, "%Y-%m-%d %H:%M:%S").date()
                        except ValueError:
                            try:
                                # Try alternative format if needed
                                month_date = datetime.strptime(month_value, "%Y-%m-%d").date()
                            except ValueError:
                                self.stdout.write(self.style.WARNING(
                                    f"Skipping row - Invalid month format: {month_value}"
                                ))
                                skipped += 1
                                continue
                    elif hasattr(month_value, 'date'):
                        # If it's already a datetime object, extract date
                        month_date = month_value.date()
                    else:
                        # If it's already a date object
                        month_date = month_value
                else:
                    self.stdout.write(self.style.WARNING("Skipping row - Missing month value"))
                    skipped += 1
                    continue

                summary, created = MonthlyCommercialSummary.objects.update_or_create(
                    sales_rep=sales_rep,
                    transformer=transformer,
                    month=month_date,
                    defaults={
                        "customers_billed": row.get("pop") or 0,
                        "customers_responded": row.get("response") or 0,
                        "revenue_billed": row.get("billed") or 0,
                        "revenue_collected": row.get("payment") or 0,
                    }
                )
            
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        total_processed = created_count + updated_count
        self.stdout.write(self.style.SUCCESS(f"\n{'='*60}"))
        self.stdout.write(self.style.SUCCESS("MONTHLY COMMERCIAL SUMMARY IMPORT COMPLETE"))
        self.stdout.write(self.style.SUCCESS(f"{'='*60}"))
        self.stdout.write(self.style.SUCCESS(f"Total rows processed: {total_processed}"))
        self.stdout.write(self.style.SUCCESS(f"New entries created: {created_count}"))
        self.stdout.write(self.style.SUCCESS(f"Existing entries updated: {updated_count}"))
        self.stdout.write(self.style.WARNING(f"Rows skipped: {skipped}"))
        self.stdout.write(self.style.SUCCESS(f"{'='*60}"))

