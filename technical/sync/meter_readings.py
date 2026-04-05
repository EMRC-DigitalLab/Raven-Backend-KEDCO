"""
technical/sync/meter_readings.py

Incremental sync: DataNest `techicalenergyreadingdailydta` → Raven `CumulativeMeterReading`.

Rules:
  - Runs every 4 hours via Celery Beat.
  - Each run covers [watermark - 48 h, now] to catch late DSO submissions.
  - On save, CumulativeMeterReading.save() auto-calculates the corresponding
    EnergyDelivered record — no extra cascade needed here.
  - Uses Django's `connections['external']` — no hardcoded credentials.
"""

from decimal import Decimal, InvalidOperation

from django.db import connections

from common.models import Feeder
from technical.models import CumulativeMeterReading
from technical.sync.base import get_sync_window

MYSQL_CHUNK = 50_000
BULK_BATCH = 10_000


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('technical_meter_readings')
    start_date = window_start.date()
    end_date = window_end.date()

    stats = {
        'window_start': window_start,
        'window_end': window_end,
        'records_fetched': 0,
        'records_created': 0,
        'records_updated': 0,
        'records_skipped': 0,
        'records_errored': 0,
        'errors': [],
    }

    feeder_map = {f.slug: f for f in Feeder.objects.filter(is_onboarded=True)}
    if not feeder_map:
        stats['errors'].append('No onboarded feeders — skipping.')
        return stats

    feeder_ids = [f.id for f in feeder_map.values()]
    existing = {}  # (feeder_id, date) → dict with pk + current values
    for r in CumulativeMeterReading.objects.filter(
        feeder_id__in=feeder_ids,
        reading_date__gte=start_date,
        reading_date__lte=end_date,
    ).values('id', 'feeder_id', 'reading_date', 'cumulative_mwh', 'is_late',
             'submission_type', 'original_submit_time', 'is_suspicious'):
        existing[(r['feeder_id'], r['reading_date'])] = r

    to_create = []
    to_update = []
    seen_keys = set()

    conn = connections['external']

    with conn.cursor() as cursor:
        offset = 0
        while True:
            cursor.execute(
                """
                SELECT energy_id, Feeder_id, Date, Energy_Reading,
                       Submitted_at, submission_type, is_late,
                       original_submit_time, is_suspicious
                FROM techicalenergyreadingdailydta
                WHERE Date >= %s AND Date <= %s
                ORDER BY Date ASC, Feeder_id ASC
                LIMIT %s OFFSET %s
                """,
                [start_date, end_date, MYSQL_CHUNK, offset],
            )
            rows = cursor.fetchall()
            if not rows:
                break

            for row in rows:
                stats['records_fetched'] += 1
                energy_id, feeder_slug, date, energy_reading, submitted_at, \
                    sub_type, is_late_raw, orig_submit, is_suspicious_raw = row

                feeder = feeder_map.get(feeder_slug)
                if not feeder:
                    stats['records_skipped'] += 1
                    continue

                if energy_reading is None:
                    stats['records_skipped'] += 1
                    continue

                try:
                    cumulative_mwh = Decimal(str(energy_reading))
                except (InvalidOperation, ValueError, TypeError):
                    stats['records_skipped'] += 1
                    continue

                sub_type = sub_type or 'dso'
                is_late = bool(is_late_raw)
                is_suspicious = bool(is_suspicious_raw)

                # Make submitted_at timezone-aware
                reading_time = None
                if submitted_at:
                    from django.utils.timezone import make_aware, is_naive
                    if is_naive(submitted_at):
                        reading_time = make_aware(submitted_at)
                    else:
                        reading_time = submitted_at

                key = (feeder.id, date)
                if key in seen_keys:
                    stats['records_skipped'] += 1
                    continue
                seen_keys.add(key)

                existing_row = existing.get(key)
                if existing_row:
                    if (
                        existing_row['cumulative_mwh'] != cumulative_mwh
                        or existing_row['is_late'] != is_late
                        or existing_row['submission_type'] != sub_type
                        or existing_row['is_suspicious'] != is_suspicious
                    ):
                        obj = CumulativeMeterReading(
                            id=existing_row['id'],
                            feeder_id=feeder.id,
                            reading_date=date,
                            cumulative_mwh=cumulative_mwh,
                            reading_time=reading_time,
                            is_late=is_late,
                            submission_type=sub_type,
                            original_submit_time=orig_submit,
                            is_suspicious=is_suspicious,
                            notes=f'Synced from DataNest (energy_id: {energy_id})',
                        )
                        to_update.append(obj)
                    else:
                        stats['records_skipped'] += 1
                else:
                    to_create.append(CumulativeMeterReading(
                        feeder=feeder,
                        reading_date=date,
                        cumulative_mwh=cumulative_mwh,
                        reading_time=reading_time,
                        is_estimated=False,
                        is_late=is_late,
                        submission_type=sub_type,
                        original_submit_time=orig_submit,
                        is_suspicious=is_suspicious,
                        notes=f'Synced from DataNest (energy_id: {energy_id})',
                    ))

                if len(to_create) >= BULK_BATCH:
                    _flush_create(to_create, stats)
                    to_create = []

                if len(to_update) >= BULK_BATCH:
                    _flush_update(to_update, stats)
                    to_update = []

            offset += len(rows)

    if to_create:
        _flush_create(to_create, stats)
    if to_update:
        _flush_update(to_update, stats)

    return stats


def _flush_create(batch: list, stats: dict):
    try:
        # Use individual saves so the model's auto EnergyDelivered calculation fires
        created = 0
        errored = 0
        for obj in batch:
            try:
                obj.save()
                created += 1
            except Exception as exc:
                errored += 1
                stats['errors'].append(f'save error ({obj.feeder_id}/{obj.reading_date}): {exc}')
        stats['records_created'] += created
        stats['records_errored'] += errored
    except Exception as exc:
        stats['records_errored'] += len(batch)
        stats['errors'].append(f'create batch error: {exc}')


def _flush_update(batch: list, stats: dict):
    try:
        # Also use individual saves so EnergyDelivered recalculation fires
        updated = 0
        errored = 0
        for obj in batch:
            try:
                obj.save()
                updated += 1
            except Exception as exc:
                errored += 1
                stats['errors'].append(f'update error ({obj.feeder_id}/{obj.reading_date}): {exc}')
        stats['records_updated'] += updated
        stats['records_errored'] += errored
    except Exception as exc:
        stats['records_errored'] += len(batch)
        stats['errors'].append(f'update batch error: {exc}')
