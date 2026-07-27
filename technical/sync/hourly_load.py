"""
technical/sync/hourly_load.py

Full CRUD incremental sync: DataNest `Technicalhourlydata` → Raven `HourlyLoad`.

Rules:
  - Non-numeric LoadS values (fault codes) → load_mw = 0
  - Every 5 minutes via Celery Beat
  - Window = [watermark - look_back, now]
  - DELETE: Raven records in the window that no longer exist in DataNest are removed
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import connections

from common.models import Feeder
from technical.models import HourlyLoad
from technical.sync.base import get_sync_window

MYSQL_CHUNK = 50_000
BULK_BATCH = 10_000


def _to_decimal_or_zero(value) -> Decimal:
    if value is None or value == '':
        return Decimal('0')
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def run_sync(override_start=None, override_end=None) -> dict:
    """
    Sync DataNest Technicalhourlydata → Raven HourlyLoad.

    override_start / override_end: date objects.  When supplied the normal
    get_sync_window() watermark logic is bypassed so callers (e.g. the
    backfill management command) can target an exact date range.
    """
    if override_start and override_end:
        from django.utils.timezone import make_aware
        import datetime as _dt
        window_start = make_aware(_dt.datetime.combine(override_start, _dt.time.min))
        window_end   = make_aware(_dt.datetime.combine(override_end,   _dt.time.max))
    else:
        window_start, window_end, _ = get_sync_window('technical_hourly_load')

    start_date = window_start.date()
    end_date   = window_end.date()

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

    feeder_map = {f.slug: f for f in Feeder.objects.filter(is_onboarded=True)}
    if not feeder_map:
        stats['errors'].append('No onboarded feeders found — skipping sync.')
        return stats

    feeder_ids = [f.id for f in feeder_map.values()]
    existing = {}
    for hl in HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__gte=start_date,
        date__lte=end_date,
    ).values('id', 'feeder_id', 'date', 'hour', 'load_mw', 'is_late', 'submission_type', 'original_submit_time'):
        existing[(hl['feeder_id'], hl['date'], hl['hour'])] = hl

    to_create = []
    to_update = []
    seen_keys = set()   # all keys seen in DataNest this run (for delete pass)

    conn = connections['external']

    with conn.cursor() as cursor:
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
                    pass
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

    # ── DELETE pass ───────────────────────────────────────────────────────────
    # Any Raven record in the window whose key was NOT seen in DataNest
    # no longer exists upstream — delete it.
    # admin_override rows are from Google Sheet / manual imports — never stale.
    stale_ids = [
        row['id']
        for key, row in existing.items()
        if key not in seen_keys and row['submission_type'] != 'admin_override'
    ]
    if stale_ids:
        try:
            deleted, _ = HourlyLoad.objects.filter(id__in=stale_ids).delete()
            stats['records_deleted'] = deleted
        except Exception as exc:
            stats['errors'].append(f'delete error: {exc}')

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
