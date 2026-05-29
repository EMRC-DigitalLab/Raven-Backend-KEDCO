"""
commercial/tasks.py

Celery tasks driving the DataNest -> Raven incremental sync for the
Commercial module. Each task:

  1. Creates a DataSyncLog row with status='running'.
  2. Calls the sync service (returns a stats dict).
  3. Finalises the log row (status=success|partial|error).

Schedules (registered in raven/celery.py):
  - sync_commercial_readings_task  -> every 5 minutes
  - sync_commercial_customers_task -> every 60 minutes
  - sync_commercial_managers_task  -> every 60 minutes
  - sync_commercial_tariff_task    -> every 60 minutes
"""

import logging

from celery import shared_task
from django.utils import timezone

from technical.models import DataSyncLog

logger = logging.getLogger(__name__)


def _run_sync(data_type: str, sync_fn, notify_on_success: bool = True):
    log = DataSyncLog.objects.create(data_type=data_type, status='running')
    try:
        stats = sync_fn()

        has_errors = bool(stats.get('errors'))
        log.status = 'partial' if has_errors else 'success'
        log.completed_at = timezone.now()
        log.window_start = stats.get('window_start')
        log.window_end = stats.get('window_end')
        log.records_fetched = stats.get('records_fetched', 0)
        log.records_created = stats.get('records_created', 0)
        log.records_updated = stats.get('records_updated', 0)
        log.records_skipped = stats.get('records_skipped', 0)
        log.records_deleted = stats.get('records_deleted', 0)
        log.records_errored = stats.get('records_errored', 0)
        log.error_message = '\n'.join(stats.get('errors', []))
        log.save()

        logger.info(
            '[CommercialSync] %s -> %s | created=%d updated=%d skipped=%d errored=%d',
            data_type, log.status,
            log.records_created, log.records_updated,
            log.records_skipped, log.records_errored,
        )

        _emit_sync_notification(data_type, log.status, log, notify_on_success)

    except Exception as exc:
        log.status = 'error'
        log.completed_at = timezone.now()
        log.error_message = str(exc)
        log.save()
        logger.exception('[CommercialSync] %s -> UNHANDLED ERROR: %s', data_type, exc)
        _emit_sync_notification(data_type, 'error', log, notify_on_success=True)
        raise


def _emit_sync_notification(data_type: str, status: str, log, notify_on_success: bool = True):
    """Call NotificationService.notify_datasync — fails silently so it never breaks a sync."""
    try:
        from notifications.services import NotificationService
        NotificationService.notify_datasync(data_type, status, log, notify_on_success)
    except Exception as exc:
        logger.warning('[CommercialSync] notify failed silently for %s: %s', data_type, exc)


@shared_task(
    name='commercial.tasks.sync_commercial_readings_task',
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def sync_commercial_readings_task(self):
    """Sync DataNest meter_readings -> MeterReading. Runs every 5 minutes."""
    from commercial.sync.readings import run_sync
    try:
        _run_sync('commercial_readings', run_sync, notify_on_success=False)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name='commercial.tasks.sync_commercial_customers_task',
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def sync_commercial_customers_task(self):
    """Sync DataNest customer_information -> CommercialCustomer. Runs every hour."""
    from commercial.sync.customers import run_sync
    try:
        _run_sync('commercial_customers', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name='commercial.tasks.sync_commercial_managers_task',
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def sync_commercial_managers_task(self):
    """Sync DataNest feeder_managers + assignments. Runs every hour."""
    from commercial.sync.managers import run_sync
    try:
        _run_sync('commercial_managers', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name='commercial.tasks.sync_commercial_tariff_task',
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def sync_commercial_tariff_task(self):
    """Sync DataNest tariff_rates -> TariffRate. Runs every hour."""
    from commercial.sync.tariff_rates import run_sync
    try:
        _run_sync('commercial_tariff_rates', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name='commercial.tasks.backfill_commercial_data_task',
    bind=True,
    max_retries=0,
    time_limit=3600,
    soft_time_limit=3300,
)
def backfill_commercial_data_task(self, start_date_str: str, end_date_str: str, tables: list):
    """
    Manual backfill of commercial data for a fixed date range.
    Triggered from POST /api/commercial/sync/backfill/.
    Readings are filtered by reading_date; customers/managers/tariff_rates do a full re-sync.
    """
    import datetime
    from django.db import connections, IntegrityError
    from django.utils.timezone import make_aware, is_naive
    from decimal import Decimal
    from commercial.models import CommercialCustomer, MeterManager, MeterManagerAssignment, MeterReading, TariffRate
    from common.models import Feeder, BusinessDistrict

    start_date = datetime.date.fromisoformat(start_date_str)
    end_date   = datetime.date.fromisoformat(end_date_str)

    TABLE_ORDER = ['tariff_rates', 'customers', 'managers', 'readings']
    active_tables = [t for t in TABLE_ORDER if t in tables]
    total_steps = len(active_tables)

    totals = {t: {'created': 0, 'updated': 0, 'skipped': 0, 'errored': 0, 'errors': []} for t in active_tables}

    def _update_meta(step, current_table=''):
        self.update_state(state='PROGRESS', meta={
            'pct':           round(step / total_steps * 100) if total_steps else 100,
            'current_table': current_table,
            'totals':        totals,
        })

    cursor = connections['external'].cursor()

    # ── Tariff Rates ──────────────────────────────────────────────────────────
    if 'tariff_rates' in active_tables:
        step = active_tables.index('tariff_rates')
        _update_meta(step, 'tariff_rates')
        t = totals['tariff_rates']
        try:
            cursor.execute("""
                SELECT id, band, customer_type, rate_per_kwh, effective_from, effective_to
                FROM tariff_rates ORDER BY id
            """)
            DN_TARIFF_MAP = {'MD1': 'MDI', 'Non-MD': 'MDNI'}
            objects = []
            for dn_id, band, dn_ctype, rate, eff_from, eff_to in cursor.fetchall():
                raven_ctype = DN_TARIFF_MAP.get(dn_ctype)
                if not raven_ctype:
                    continue
                objects.append(TariffRate(
                    band=band, customer_type=raven_ctype, rate_per_kwh=rate,
                    effective_from=eff_from, effective_to=eff_to,
                    is_active=(eff_to is None), datanest_id=dn_id,
                ))
            TariffRate.objects.bulk_create(
                objects, update_conflicts=True, unique_fields=['datanest_id'],
                update_fields=['band', 'customer_type', 'rate_per_kwh', 'effective_from', 'effective_to', 'is_active'],
            )
            t['created'] = len(objects)
        except Exception as exc:
            t['errors'].append(str(exc))
            logger.exception('[CommercialBackfill] tariff_rates error: %s', exc)

    # ── Customers ─────────────────────────────────────────────────────────────
    if 'customers' in active_tables:
        step = active_tables.index('customers')
        _update_meta(step, 'customers')
        t = totals['customers']
        try:
            cursor.execute("""
                SELECT customer_id, account_no, meter_number, customer_name,
                       customer_address, phone_number, feeder_id, district_id,
                       customer_type, created_at,
                       meter_status, meter_fault_logged_at, meter_fault_logged_by
                FROM customer_information ORDER BY created_at
            """)
            feeder_cache   = {f.slug: f for f in Feeder.objects.all()}
            district_cache = {d.slug: d for d in BusinessDistrict.objects.all()}
            DN_CTYPE_MAP = {'MDI': 'MDI', 'MDNI': 'MDNI'}

            def _feeder(code):
                if not code:
                    return None
                return feeder_cache.get(code) or feeder_cache.get(code.lower())

            def _district(code):
                if not code:
                    return None
                return district_cache.get(code) or district_cache.get(code.lower())

            objects, seen = [], set()
            for ext_id, acct, meter, name, addr, phone, feeder_id, dist_id, ctype, created_at, meter_status, fault_at, fault_by in cursor.fetchall():
                raven_ctype = DN_CTYPE_MAP.get(ctype)
                if not raven_ctype or ext_id in seen:
                    t['skipped'] += 1
                    continue
                seen.add(ext_id)
                status_val = meter_status or 'active'
                objects.append(CommercialCustomer(
                    external_id=ext_id, account_no=acct or '', meter_number=meter or '',
                    customer_name=name or '', customer_address=addr or '', phone_number=phone or '',
                    feeder=_feeder(feeder_id), district=_district(dist_id), customer_type=raven_ctype,
                    datanest_created_at=make_aware(created_at) if created_at and is_naive(created_at) else created_at,
                    meter_status=status_val,
                    meter_fault_logged_at=make_aware(fault_at) if fault_at and is_naive(fault_at) else fault_at,
                    meter_fault_logged_by=fault_by or '',
                    is_bypass=(status_val == 'bypassed'),
                ))
            CommercialCustomer.objects.bulk_create(
                objects, update_conflicts=True, unique_fields=['external_id'],
                update_fields=[
                    'account_no', 'meter_number', 'customer_name', 'customer_address',
                    'phone_number', 'feeder_id', 'district_id', 'customer_type', 'datanest_created_at',
                    'meter_status', 'meter_fault_logged_at', 'meter_fault_logged_by', 'is_bypass',
                ],
                batch_size=500,
            )
            t['created'] = len(objects)
        except Exception as exc:
            t['errors'].append(str(exc))
            logger.exception('[CommercialBackfill] customers error: %s', exc)

    # ── Managers + Assignments ────────────────────────────────────────────────
    if 'managers' in active_tables:
        step = active_tables.index('managers')
        _update_meta(step, 'managers')
        t = totals['managers']
        try:
            cursor.execute("""
                SELECT manager_id, user_id, staff_id, meter_reader_name, manager_type
                FROM feeder_managers ORDER BY created_at
            """)
            mgr_objects, seen = [], set()
            for ext_id, user_id, staff_id, name, mtype in cursor.fetchall():
                if ext_id in seen or mtype not in ('MDI', 'MDNI'):
                    continue
                seen.add(ext_id)
                mgr_objects.append(MeterManager(
                    external_id=ext_id, user_external_id=user_id or '',
                    staff_id=staff_id or '', name=name or '', manager_type=mtype,
                ))
            MeterManager.objects.bulk_create(
                mgr_objects, update_conflicts=True, unique_fields=['external_id'],
                update_fields=['user_external_id', 'staff_id', 'name', 'manager_type'],
                batch_size=500,
            )

            cursor.execute("""
                SELECT assignment_id, manager_id, feeder_id, status, start_date, end_date
                FROM feeder_manager_assignments ORDER BY assigned_at
            """)
            manager_cache = {m.external_id: m for m in MeterManager.objects.all()}
            feeder_cache  = {f.slug: f for f in Feeder.objects.all()}
            asgn_objects, seen = [], set()
            for ext_id, mgr_ext_id, feeder_id, status_val, start_d, end_d in cursor.fetchall():
                if ext_id in seen:
                    continue
                mgr    = manager_cache.get(mgr_ext_id)
                feeder = feeder_cache.get(feeder_id) or feeder_cache.get((feeder_id or '').lower())
                if not mgr or not feeder:
                    t['skipped'] += 1
                    continue
                seen.add(ext_id)
                asgn_objects.append(MeterManagerAssignment(
                    external_id=ext_id, manager=mgr, feeder=feeder,
                    status=status_val or 'active', start_date=start_d, end_date=end_d,
                ))
            MeterManagerAssignment.objects.bulk_create(
                asgn_objects, update_conflicts=True, unique_fields=['external_id'],
                update_fields=['status', 'start_date', 'end_date'],
                batch_size=500,
            )
            t['created'] = len(mgr_objects) + len(asgn_objects)
        except Exception as exc:
            t['errors'].append(str(exc))
            logger.exception('[CommercialBackfill] managers error: %s', exc)

    # ── Readings (date-range filtered) ────────────────────────────────────────
    if 'readings' in active_tables:
        step = active_tables.index('readings')
        _update_meta(step, 'readings')
        t = totals['readings']
        try:
            customer_map = {
                str(c['external_id']): (c['id'], c['customer_type'])
                for c in CommercialCustomer.objects.values('id', 'external_id', 'customer_type')
            }
            cursor.execute(
                """
                SELECT
                    mr.reading_id, mr.customer_id, mr.reading_date, mr.reading_time,
                    mr.previous_reading, mr.present_reading,
                    mr.multiplier_factor, mr.billed_consumption,
                    mr.tariff_rate, mr.reading_type,
                    mr.recorded_by,
                    mr.gps_latitude, mr.gps_longitude, mr.gps_location_name,
                    mr.proof_file_id, mr.observation,
                    mr.gis_id, mr.gis_match,
                    mr.ocr_status, mr.ocr_extracted_value, mr.ocr_confidence,
                    mr.audit_status, mr.audited_by, mr.audit_note, mr.audited_at,
                    mr.submission_status, mr.fault_source, mr.estimation_method,
                    mr.created_at,
                    fm.meter_reader_name
                FROM meter_readings mr
                LEFT JOIN feeder_managers fm ON mr.recorded_by = fm.user_id
                WHERE mr.reading_date >= %s AND mr.reading_date <= %s
                ORDER BY mr.reading_date ASC
                """,
                [start_date, end_date],
            )
            rows = cursor.fetchall()
            t['skipped'] += max(0, len(rows) - len(customer_map))

            seen = set()
            for row in rows:
                (
                    reading_id, customer_id, reading_date, reading_time,
                    prev_r, pres_r, multiplier, billed_cons,
                    tariff_rate, reading_type,
                    recorded_by_id,
                    lat, lng, loc_name, proof_id, obs,
                    gis_id, gis_match,
                    ocr_status, ocr_val, ocr_conf,
                    audit_status, audited_by, audit_note, audited_at,
                    submission_status, fault_source, estimation_method,
                    created_at, reader_name,
                ) = row

                ext_id = str(reading_id)
                if ext_id in seen:
                    t['skipped'] += 1
                    continue

                customer_info = customer_map.get(str(customer_id))
                if not customer_info or pres_r is None:
                    t['skipped'] += 1
                    continue

                customer_pk, customer_type = customer_info
                seen.add(ext_id)

                if created_at and is_naive(created_at):
                    created_at = make_aware(created_at)
                if audited_at and is_naive(audited_at):
                    audited_at = make_aware(audited_at)

                valid_types = {'MDI', 'MDNI'}
                r_type = reading_type if reading_type in valid_types else customer_type

                # Compute consumption/billed_consumption when DataNest leaves them NULL
                consumption = None
                if pres_r is not None and prev_r is not None:
                    consumption = pres_r - prev_r
                if billed_cons is None and consumption is not None:
                    factor = multiplier if multiplier is not None else 1
                    billed_cons = consumption * factor

                obj = MeterReading(
                    external_id=ext_id,
                    customer_id=customer_pk,
                    reading_date=reading_date,
                    reading_time=reading_time,
                    previous_reading=prev_r,
                    present_reading=pres_r,
                    consumption=consumption,
                    multiplier_factor=multiplier,
                    billed_consumption=billed_cons,
                    tariff_rate=tariff_rate,
                    reading_type=r_type,
                    recorded_by_id=str(recorded_by_id) if recorded_by_id else '',
                    recorded_by_name=reader_name or '',
                    gps_latitude=lat,
                    gps_longitude=lng,
                    gps_location_name=loc_name or '',
                    has_proof=bool(proof_id),
                    observation=obs or '',
                    gis_id=gis_id or '',
                    gis_match=gis_match,
                    ocr_status=ocr_status or 'pending',
                    ocr_extracted_value=ocr_val,
                    ocr_confidence=ocr_conf,
                    audit_status=audit_status,
                    audited_by=audited_by or '',
                    audit_note=audit_note or '',
                    audited_at=audited_at,
                    submission_status=submission_status or 'on_time',
                    fault_source=fault_source or None,
                    estimation_method=estimation_method or '',
                    datanest_created_at=created_at,
                )

                try:
                    obj.save(force_insert=True)
                    t['created'] += 1
                except IntegrityError:
                    try:
                        existing = MeterReading.objects.get(customer_id=customer_pk, reading_date=reading_date)
                        obj.id = existing.id
                        obj.save(force_update=True)
                        t['updated'] += 1
                    except Exception as exc2:
                        t['errored'] += 1
                        t['errors'].append(f'{reading_date}/{customer_pk}: {exc2}')
                except Exception as exc2:
                    t['errored'] += 1
                    t['errors'].append(f'{reading_date}/{customer_pk}: {exc2}')

        except Exception as exc:
            t['errors'].append(str(exc))
            logger.exception('[CommercialBackfill] readings error: %s', exc)

    _update_meta(total_steps)
    return {
        'status':     'done',
        'start_date': start_date_str,
        'end_date':   end_date_str,
        'tables':     active_tables,
        'totals':     totals,
    }
