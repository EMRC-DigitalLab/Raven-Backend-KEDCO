"""
Sync commercial data from DataNest (external MySQL) into Raven (internal PostgreSQL).

Uses bulk_create with update_conflicts for fast upserts — no row-by-row inserts.

Usage:
  python manage.py sync_commercial_data
  python manage.py sync_commercial_data --table tariff_rates
  python manage.py sync_commercial_data --table customers
  python manage.py sync_commercial_data --table readings
  python manage.py sync_commercial_data --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import connections, transaction
from django.utils import timezone

from commercial.models import CommercialCustomer, MeterManager, MeterManagerAssignment, MeterReading, TariffRate
from common.models import BusinessDistrict, Feeder


def make_aware(dt):
    """Convert naive datetime to timezone-aware."""
    if dt is None:
        return None
    if timezone.is_aware(dt):
        return dt
    return timezone.make_aware(dt)

# DataNest customer_type → Raven customer_type mapping
DN_CUSTOMER_TYPE_MAP = {
    'MDI':  'MDI',
    'MDNI': 'MDNI',
}

# DataNest tariff_rates customer_type → Raven customer_type mapping
# MD2 is skipped — Raven uses MD1 as the canonical MDI rate
DN_TARIFF_TYPE_MAP = {
    'MD1':    'MDI',
    'Non-MD': 'MDNI',
}

BATCH_SIZE = 500


class Command(BaseCommand):
    help = 'Sync commercial data from DataNest into Raven (bulk insert)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--table',
            choices=['tariff_rates', 'customers', 'readings', 'managers', 'all'],
            default='all',
        )
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        table   = options['table']
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing will be saved\n'))

        cursor = connections['external'].cursor()

        if table in ('tariff_rates', 'all'):
            self._sync_tariff_rates(cursor, dry_run)

        if table in ('customers', 'all'):
            self._sync_customers(cursor, dry_run)

        if table in ('readings', 'all'):
            self._sync_readings(cursor, dry_run)

        if table in ('managers', 'all'):
            self._sync_managers(cursor, dry_run)

        self.stdout.write(self.style.SUCCESS('\nSync complete.'))

    # ── Tariff Rates ──────────────────────────────────────────────────────────

    def _sync_tariff_rates(self, cursor, dry_run):
        self.stdout.write('\n=== Syncing TariffRate ===')

        cursor.execute("""
            SELECT id, band, customer_type, rate_per_kwh, effective_from, effective_to
            FROM tariff_rates ORDER BY id
        """)
        rows = cursor.fetchall()
        self.stdout.write(f'Found {len(rows)} records in DataNest')

        objects = []
        for dn_id, band, dn_ctype, rate, eff_from, eff_to in rows:
            raven_ctype = DN_TARIFF_TYPE_MAP.get(dn_ctype)
            if not raven_ctype:
                continue
            objects.append(TariffRate(
                band=band,
                customer_type=raven_ctype,
                rate_per_kwh=rate,
                effective_from=eff_from,
                effective_to=eff_to,
                is_active=(eff_to is None),
                datanest_id=dn_id,
            ))

        if dry_run:
            self.stdout.write(f'  Would upsert {len(objects)} records')
            return

        TariffRate.objects.bulk_create(
            objects,
            update_conflicts=True,
            unique_fields=['datanest_id'],
            update_fields=['band', 'customer_type', 'rate_per_kwh', 'effective_from', 'effective_to', 'is_active'],
        )
        self.stdout.write(f'  Upserted {len(objects)} tariff rates')

    # ── Customers ─────────────────────────────────────────────────────────────

    def _sync_customers(self, cursor, dry_run):
        self.stdout.write('\n=== Syncing CommercialCustomer ===')

        cursor.execute("""
            SELECT customer_id, account_no, meter_number, customer_name,
                   customer_address, phone_number, feeder_id, district_id,
                   customer_type, created_at,
                   meter_status, meter_fault_logged_at, meter_fault_logged_by
            FROM customer_information
            ORDER BY created_at
        """)
        rows = cursor.fetchall()
        self.stdout.write(f'Found {len(rows)} customers in DataNest')

        feeder_cache   = {f.slug: f for f in Feeder.objects.all()}
        district_cache = {d.slug: d for d in BusinessDistrict.objects.all()}
        # DataNest feeder/district codes are uppercase (e.g. JG-DUT-DUT).
        # Raven slugs are lowercase. Normalise the lookup key.
        def _feeder(code):
            if not code:
                return None
            return feeder_cache.get(code) or feeder_cache.get(code.lower())
        def _district(code):
            if not code:
                return None
            return district_cache.get(code) or district_cache.get(code.lower())

        objects = []
        skipped = 0
        seen    = set()  # deduplicate by external_id within DataNest data

        for ext_id, acct, meter, name, addr, phone, feeder_id, dist_id, ctype, created_at, meter_status, fault_logged_at, fault_logged_by in rows:
            raven_ctype = DN_CUSTOMER_TYPE_MAP.get(ctype)
            if not raven_ctype or ext_id in seen:
                skipped += 1
                continue
            seen.add(ext_id)

            status = meter_status or 'active'
            objects.append(CommercialCustomer(
                external_id=ext_id,
                account_no=acct or '',
                meter_number=meter or '',
                customer_name=name or '',
                customer_address=addr or '',
                phone_number=phone or '',
                feeder=_feeder(feeder_id),
                district=_district(dist_id),
                customer_type=raven_ctype,
                datanest_created_at=make_aware(created_at),
                meter_status=status,
                meter_fault_logged_at=make_aware(fault_logged_at) if fault_logged_at else None,
                meter_fault_logged_by=fault_logged_by or '',
                is_bypass=(status == 'bypassed'),
            ))

        self.stdout.write(f'  Unique customers to upsert: {len(objects)} | Skipped: {skipped}')

        if dry_run:
            return

        update_fields = [
            'account_no', 'meter_number', 'customer_name', 'customer_address',
            'phone_number', 'feeder_id', 'district_id', 'customer_type', 'datanest_created_at',
            'meter_status', 'meter_fault_logged_at', 'meter_fault_logged_by', 'is_bypass',
        ]

        with transaction.atomic():
            CommercialCustomer.objects.bulk_create(
                objects,
                update_conflicts=True,
                unique_fields=['external_id'],
                update_fields=update_fields,
                batch_size=BATCH_SIZE,
            )
        self.stdout.write(f'  Done — {len(objects)} customers upserted')

    # ── Meter Readings ────────────────────────────────────────────────────────

    def _sync_readings(self, cursor, dry_run):
        self.stdout.write('\n=== Syncing MeterReading ===')

        cursor.execute("""
            SELECT
                mr.reading_id, mr.customer_id, mr.reading_date, mr.reading_time,
                mr.previous_reading, mr.present_reading, mr.consumption,
                mr.multiplier_factor, mr.billed_consumption, mr.tariff_rate,
                mr.reading_type, mr.recorded_by, mr.created_at, mr.proof_file_id,
                mr.gps_latitude, mr.gps_longitude, mr.gps_location_name, mr.observation,
                COALESCE(u.first_name, '') AS recorded_by_name,
                mr.gis_id, mr.gis_match,
                mr.ocr_status, mr.ocr_extracted_value, mr.ocr_confidence,
                mr.audit_status, mr.audited_by, mr.audit_note, mr.audited_at,
                mr.submission_status, mr.fault_source, mr.estimation_method
            FROM meter_readings mr
            LEFT JOIN users u ON mr.recorded_by = u.user_id
            ORDER BY mr.reading_date ASC
        """)
        rows = cursor.fetchall()
        self.stdout.write(f'Found {len(rows)} meter readings in DataNest')

        customer_cache = {c.external_id: c.id for c in CommercialCustomer.objects.only('id', 'external_id')}

        objects = []
        skipped = 0
        seen    = set()

        for row in rows:
            (
                ext_id, cust_ext_id, rdate, rtime,
                prev_r, pres_r, cons, mult, billed_cons,
                tariff, rtype, rec_by_id, created_at,
                proof_id, lat, lon, loc_name, obs, rec_by_name,
                gis_id, gis_match,
                ocr_status, ocr_extracted_value, ocr_confidence,
                audit_status, audited_by, audit_note, audited_at,
                submission_status, fault_source, estimation_method,
            ) = row

            if ext_id in seen:
                skipped += 1
                continue

            cust_id = customer_cache.get(cust_ext_id)
            if not cust_id:
                skipped += 1
                continue

            raven_rtype = DN_CUSTOMER_TYPE_MAP.get(rtype)
            if not raven_rtype:
                skipped += 1
                continue

            seen.add(ext_id)
            objects.append(MeterReading(
                external_id=ext_id,
                customer_id=cust_id,
                reading_date=rdate,
                reading_time=rtime,
                previous_reading=prev_r,
                present_reading=pres_r,
                consumption=cons,
                multiplier_factor=mult,
                billed_consumption=billed_cons,
                tariff_rate=tariff,
                reading_type=raven_rtype,
                recorded_by_id=rec_by_id or '',
                recorded_by_name=rec_by_name or '',
                has_proof=(proof_id is not None),
                gps_latitude=lat,
                gps_longitude=lon,
                gps_location_name=loc_name or '',
                observation=obs or '',
                datanest_created_at=make_aware(created_at),
                # GIS
                gis_id=gis_id or '',
                gis_match=(bool(gis_match) if gis_match is not None else None),
                # OCR
                ocr_status=ocr_status or 'pending',
                ocr_extracted_value=ocr_extracted_value,
                ocr_confidence=ocr_confidence,
                # Audit
                audit_status=audit_status or None,
                audited_by=audited_by or '',
                audit_note=audit_note or '',
                audited_at=make_aware(audited_at) if audited_at else None,
                # Submission + fault metadata
                submission_status=submission_status or 'on_time',
                fault_source=fault_source or None,
                estimation_method=estimation_method or '',
            ))

        self.stdout.write(f'  Unique readings to upsert: {len(objects)} | Skipped: {skipped}')

        if dry_run:
            return

        update_fields = [
            'reading_date', 'reading_time', 'previous_reading', 'present_reading',
            'consumption', 'multiplier_factor', 'billed_consumption', 'tariff_rate',
            'reading_type', 'recorded_by_id', 'recorded_by_name', 'has_proof',
            'gps_latitude', 'gps_longitude', 'gps_location_name', 'observation', 'datanest_created_at',
            # GIS
            'gis_id', 'gis_match',
            # OCR
            'ocr_status', 'ocr_extracted_value', 'ocr_confidence',
            # Audit
            'audit_status', 'audited_by', 'audit_note', 'audited_at',
            # Submission + fault metadata
            'submission_status', 'fault_source', 'estimation_method',
        ]

        with transaction.atomic():
            MeterReading.objects.bulk_create(
                objects,
                update_conflicts=True,
                unique_fields=['external_id'],
                update_fields=update_fields,
                batch_size=BATCH_SIZE,
            )
        self.stdout.write(f'  Done — {len(objects)} readings upserted')

    # ── Meter Managers + Assignments ──────────────────────────────────────────

    def _sync_managers(self, cursor, dry_run):
        self.stdout.write('\n=== Syncing MeterManager ===')

        # Sync managers
        cursor.execute("""
            SELECT manager_id, user_id, staff_id, meter_reader_name, manager_type
            FROM feeder_managers
            ORDER BY created_at
        """)
        rows = cursor.fetchall()
        self.stdout.write(f'Found {len(rows)} managers in DataNest')

        manager_objects = []
        seen = set()
        for ext_id, user_id, staff_id, name, mtype in rows:
            if ext_id in seen or mtype not in ('MDI', 'MDNI'):
                continue
            seen.add(ext_id)
            manager_objects.append(MeterManager(
                external_id=ext_id,
                user_external_id=user_id or '',
                staff_id=staff_id or '',
                name=name or '',
                manager_type=mtype,
            ))

        if not dry_run:
            with transaction.atomic():
                MeterManager.objects.bulk_create(
                    manager_objects,
                    update_conflicts=True,
                    unique_fields=['external_id'],
                    update_fields=['user_external_id', 'staff_id', 'name', 'manager_type'],
                    batch_size=BATCH_SIZE,
                )
        self.stdout.write(f'  Done — {len(manager_objects)} managers upserted')

        # Sync assignments
        self.stdout.write('\n=== Syncing MeterManagerAssignment ===')
        cursor.execute("""
            SELECT assignment_id, manager_id, feeder_id, status, start_date, end_date
            FROM feeder_manager_assignments
            ORDER BY assigned_at
        """)
        rows = cursor.fetchall()
        self.stdout.write(f'Found {len(rows)} assignments in DataNest')

        manager_cache = {m.external_id: m for m in MeterManager.objects.all()}
        feeder_cache  = {f.slug: f for f in Feeder.objects.all()}

        assignment_objects = []
        skipped = 0
        seen = set()
        for ext_id, mgr_ext_id, feeder_id, status, start_date, end_date in rows:
            if ext_id in seen:
                continue
            manager = manager_cache.get(mgr_ext_id)
            feeder  = feeder_cache.get(feeder_id)
            if not manager or not feeder:
                skipped += 1
                continue
            seen.add(ext_id)
            assignment_objects.append(MeterManagerAssignment(
                external_id=ext_id,
                manager=manager,
                feeder=feeder,
                status=status or 'active',
                start_date=start_date,
                end_date=end_date,
            ))

        if not dry_run:
            with transaction.atomic():
                MeterManagerAssignment.objects.bulk_create(
                    assignment_objects,
                    update_conflicts=True,
                    unique_fields=['external_id'],
                    update_fields=['status', 'start_date', 'end_date'],
                    batch_size=BATCH_SIZE,
                )
        self.stdout.write(f'  Done — {len(assignment_objects)} assignments upserted | Skipped: {skipped}')
