"""
commercial/sync/readings.py

Incremental sync: DataNest `meter_readings` -> Raven `MeterReading`.

Rules:
  - Runs every 5 minutes via Celery Beat.
  - Each run covers [watermark - 4h, now] to catch late field officer submissions.
  - Upserts by external_id (DataNest reading_id). Also respects the
    (customer, reading_date) unique constraint — a second reading for the
    same customer+date in DataNest will overwrite the first.
  - energy_charge / vat / total_billed_amount are @property on the model,
    so they are NOT stored — just billed_consumption + tariff_rate.
"""

from datetime import timedelta

from django.db import connections, IntegrityError
from django.utils.timezone import make_aware, is_naive

from commercial.models import CommercialCustomer, MeterReading
from technical.sync.base import get_sync_window

CHUNK = 10_000
BATCH = 2_000
LOOK_BACK_HOURS = 4


def run_sync() -> dict:
    window_start, window_end, watermark = get_sync_window('commercial_readings')

    # Always look back at least LOOK_BACK_HOURS so late submissions are captured
    earliest = window_end - timedelta(hours=LOOK_BACK_HOURS)
    if window_start > earliest:
        window_start = earliest

    stats = {
        'window_start': window_start,
        'window_end': window_end,
        'records_fetched': 0,
        'records_created': 0,
        'records_updated': 0,
        'records_skipped': 0,
        'records_deleted': 0,
        'records_errored': 0,
        'errors': [],
    }

    # customer external_id (str) -> (pk, customer_type)
    customer_map = {
        str(c['external_id']): (c['id'], c['customer_type'])
        for c in CommercialCustomer.objects.values('id', 'external_id', 'customer_type')
    }
    if not customer_map:
        stats['errors'].append('No commercial customers in Raven — skipping.')
        return stats

    # existing readings in window: external_id -> {id, present_reading, ...}
    existing = {
        str(r['external_id']): r
        for r in MeterReading.objects.filter(
            datanest_created_at__gte=window_start,
            datanest_created_at__lte=window_end,
        ).values('id', 'external_id', 'present_reading', 'previous_reading',
                 'submission_status', 'fault_source', 'estimation_method',
                 'ocr_status', 'audit_status')
    }

    to_create = []
    to_update = []
    seen_ext_ids = set()

    conn = connections['external']

    with conn.cursor() as cursor:
        offset = 0
        while True:
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
                WHERE mr.created_at >= %s AND mr.created_at <= %s
                ORDER BY mr.created_at ASC
                LIMIT %s OFFSET %s
                """,
                [window_start, window_end, CHUNK, offset],
            )
            rows = cursor.fetchall()
            if not rows:
                break

            for row in rows:
                stats['records_fetched'] += 1
                (
                    reading_id, customer_id, reading_date, reading_time,
                    prev_reading, pres_reading,
                    multiplier, billed_consumption,
                    tariff_rate, reading_type,
                    recorded_by_id,
                    lat, lng, loc_name,
                    proof_file_id, observation,
                    gis_id, gis_match,
                    ocr_status, ocr_val, ocr_conf,
                    audit_status, audited_by, audit_note, audited_at,
                    submission_status, fault_source, estimation_method,
                    created_at,
                    reader_name,
                ) = row

                customer_info = customer_map.get(str(customer_id))
                if not customer_info:
                    stats['records_skipped'] += 1
                    continue

                customer_pk, customer_type = customer_info

                if pres_reading is None:
                    stats['records_skipped'] += 1
                    continue

                ext_id = str(reading_id)
                if ext_id in seen_ext_ids:
                    stats['records_skipped'] += 1
                    continue
                seen_ext_ids.add(ext_id)

                if created_at and is_naive(created_at):
                    created_at = make_aware(created_at)
                if audited_at and is_naive(audited_at):
                    audited_at = make_aware(audited_at)

                # reading_type from DataNest if valid, else from customer type
                valid_types = {'MDI', 'MDNI'}
                r_type = reading_type if reading_type in valid_types else customer_type

                # Compute consumption/billed_consumption when DataNest leaves them NULL
                consumption = None
                if pres_reading is not None and prev_reading is not None:
                    consumption = pres_reading - prev_reading
                if billed_consumption is None and consumption is not None:
                    factor = multiplier if multiplier is not None else 1
                    billed_consumption = consumption * factor

                kwargs = dict(
                    external_id=ext_id,
                    customer_id=customer_pk,
                    reading_date=reading_date,
                    reading_time=reading_time,
                    previous_reading=prev_reading,
                    present_reading=pres_reading,
                    consumption=consumption,
                    multiplier_factor=multiplier,
                    billed_consumption=billed_consumption,
                    tariff_rate=tariff_rate,
                    reading_type=r_type,
                    recorded_by_id=str(recorded_by_id) if recorded_by_id else '',
                    recorded_by_name=reader_name or '',
                    gps_latitude=lat,
                    gps_longitude=lng,
                    gps_location_name=loc_name or '',
                    has_proof=bool(proof_file_id),
                    observation=observation or '',
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

                existing_row = existing.get(ext_id)
                if existing_row:
                    changed = (
                        existing_row['present_reading'] != pres_reading
                        or existing_row['submission_status'] != (submission_status or 'on_time')
                        or existing_row['fault_source'] != (fault_source or None)
                        or existing_row['estimation_method'] != (estimation_method or '')
                        or existing_row['ocr_status'] != (ocr_status or 'pending')
                        or existing_row['audit_status'] != audit_status
                    )
                    if changed:
                        to_update.append(MeterReading(id=existing_row['id'], **kwargs))
                    else:
                        stats['records_skipped'] += 1
                else:
                    to_create.append(MeterReading(**kwargs))

                if len(to_create) >= BATCH:
                    _flush_create(to_create, stats)
                    to_create = []
                if len(to_update) >= BATCH:
                    _flush_update(to_update, stats)
                    to_update = []

            offset += len(rows)

    if to_create:
        _flush_create(to_create, stats)
    if to_update:
        _flush_update(to_update, stats)

    return stats


def _flush_create(batch, stats):
    for obj in batch:
        try:
            obj.save(force_insert=True)
            stats['records_created'] += 1
        except IntegrityError:
            # (customer, reading_date) conflict — update instead
            try:
                existing = MeterReading.objects.get(
                    customer_id=obj.customer_id,
                    reading_date=obj.reading_date,
                )
                obj.id = existing.id
                obj.save(force_update=True)
                stats['records_updated'] += 1
            except Exception as exc:
                stats['records_errored'] += 1
                stats['errors'].append(f'conflict-update error: {exc}')
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'create error ({obj.customer_id}/{obj.reading_date}): {exc}')


def _flush_update(batch, stats):
    for obj in batch:
        try:
            obj.save(force_update=True)
            stats['records_updated'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'update error ({obj.customer_id}/{obj.reading_date}): {exc}')
