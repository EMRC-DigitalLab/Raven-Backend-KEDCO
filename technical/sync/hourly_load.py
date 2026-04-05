"""
technical/sync/hourly_load.py

Incremental sync: DataNest `Technicalhourlydata` → Raven `HourlyLoad`.

Rules agreed with the team:
  - Non-numeric LoadS values (fault codes) are stored as load_mw = 0.
  - Runs every 15 minutes via Celery Beat.
  - Each run covers [watermark - 24 h, now] to catch late DSO submissions.
  - Uses Django's `connections['external']` (MySQL) — no hardcoded credentials.
  - Streams rows in server-side chunks to handle large volumes without OOM.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import connections

from common.models import Feeder
from technical.models import HourlyLoad
from technical.sync.base import get_sync_window

# MySQL chunk size — rows fetched per round-trip
MYSQL_CHUNK = 50_000

# Django bulk_create / bulk_update batch size
BULK_BATCH = 10_000


def _to_decimal_or_zero(value) -> Decimal:
    """Return the numeric value as Decimal, or Decimal(0) for fault codes."""
    if value is None or value == '':
        return Decimal('0')
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def run_sync() -> dict:
    """
    Perform one incremental sync of hourly load data.

    Returns a stats dict consumed by the Celery task to write the DataSyncLog.
    """
    window_start, window_end, _ = get_sync_window('technical_hourly_load')
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

    # Pre-load onboarded feeders (slug → Feeder)
    feeder_map = {f.slug: f for f in Feeder.objects.filter(is_onboarded=True)}
    if not feeder_map:
        stats['errors'].append('No onboarded feeders found — skipping sync.')
        return stats

    # Load existing (feeder_id, date, hour) keys for the window to drive
    # create-vs-update decisions without per-row DB hits.
    feeder_ids = [f.id for f in feeder_map.values()]
    existing = {}  # (feeder_id, date, hour) → HourlyLoad pk
    for hl in HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__gte=start_date,
        date__lte=end_date,
    ).values('id', 'feeder_id', 'date', 'hour', 'load_mw', 'is_late', 'submission_type', 'original_submit_time'):
        existing[(hl['feeder_id'], hl['date'], hl['hour'])] = hl

    to_create = []
    to_update = []   # list of (HourlyLoad instance to update, changed fields)
    seen_keys = set()

    conn = connections['external']

    with conn.cursor() as cursor:
        # Stream in chunks ordered by feeder + date + hour
        offset = 0
        while True:
            cursor.execute(
                """
                SELECT feeder_id, Date, Hour_d, LoadS,
                       submission_type, is_late, original_submit_time
                FROM Technicalhourlydata
                WHERE Date >= %s AND Date <= %s
                ORDER BY feeder_id, Date, Hour_d
                LIMIT %s OFFSET %s
                """,
                [start_date, end_date, MYSQL_CHUNK, offset],
            )
            rows = cursor.fetchall()
            if not rows:
                break

            for row in rows:
                stats['records_fetched'] += 1
                feeder_slug, date, hour, load_value, sub_type, is_late_raw, orig_submit = row

                feeder = feeder_map.get(feeder_slug)
                if not feeder:
                    stats['records_skipped'] += 1
                    continue

                if not isinstance(date, datetime) and hasattr(date, 'year'):
                    pass  # already a date
                hour = int(hour) if hour is not None else 0
                if hour < 0 or hour > 23:
                    stats['records_skipped'] += 1
                    continue

                load_mw = _to_decimal_or_zero(load_value)
                sub_type = sub_type or 'dso'
                is_late = bool(is_late_raw)

                key = (feeder.id, date, hour)
                if key in seen_keys:
                    stats['records_skipped'] += 1
                    continue
                seen_keys.add(key)

                existing_row = existing.get(key)
                if existing_row:
                    # Only queue update when something actually changed
                    if (
                        existing_row['load_mw'] != load_mw
                        or existing_row['is_late'] != is_late
                        or existing_row['submission_type'] != sub_type
                        or existing_row['original_submit_time'] != orig_submit
                    ):
                        obj = HourlyLoad(
                            id=existing_row['id'],
                            feeder_id=feeder.id,
                            date=date,
                            hour=hour,
                            load_mw=load_mw,
                            is_late=is_late,
                            submission_type=sub_type,
                            original_submit_time=orig_submit,
                        )
                        to_update.append(obj)
                    else:
                        stats['records_skipped'] += 1
                else:
                    to_create.append(HourlyLoad(
                        feeder=feeder,
                        date=date,
                        hour=hour,
                        load_mw=load_mw,
                        is_late=is_late,
                        submission_type=sub_type,
                        original_submit_time=orig_submit,
                    ))

                # Flush creates
                if len(to_create) >= BULK_BATCH:
                    _flush_create(to_create, stats)
                    to_create = []

                # Flush updates
                if len(to_update) >= BULK_BATCH:
                    _flush_update(to_update, stats)
                    to_update = []

            offset += len(rows)

    # Final flush
    if to_create:
        _flush_create(to_create, stats)
    if to_update:
        _flush_update(to_update, stats)

    return stats


def _flush_create(batch: list, stats: dict):
    try:
        HourlyLoad.objects.bulk_create(
            batch,
            batch_size=BULK_BATCH,
            ignore_conflicts=True,
        )
        stats['records_created'] += len(batch)
    except Exception as exc:
        stats['records_errored'] += len(batch)
        stats['errors'].append(f'bulk_create error: {exc}')


def _flush_update(batch: list, stats: dict):
    try:
        HourlyLoad.objects.bulk_update(
            batch,
            fields=['load_mw', 'is_late', 'submission_type', 'original_submit_time'],
            batch_size=BULK_BATCH,
        )
        stats['records_updated'] += len(batch)
    except Exception as exc:
        stats['records_errored'] += len(batch)
        stats['errors'].append(f'bulk_update error: {exc}')
