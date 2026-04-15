"""
energy_account/sync/meter_check_schedules.py

Incremental sync: DataNest `ea_meter_check_schedules` → Raven `EAMeterCheckSchedule`.

Mapping:
  station_id → InjectionSubstation.slug
"""

from django.db import connections
from django.utils.timezone import is_naive, make_aware

from common.models import InjectionSubstation
from energy_account.models import EAMeterCheckSchedule
from technical.sync.base import get_sync_window


def _aware(dt):
    if dt and is_naive(dt):
        return make_aware(dt)
    return dt


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_meter_check_schedules')

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

    station_map = {s.slug: s for s in InjectionSubstation.objects.all()}
    existing    = {s.datanest_id: s for s in EAMeterCheckSchedule.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT schedule_id, station_id, week_label, scheduled_date,
                   assigned_by, status, created_at, updated_at
            FROM ea_meter_check_schedules
            WHERE updated_at >= %s AND updated_at <= %s
            ORDER BY created_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            schedule_id, station_id, week_label, scheduled_date,
            assigned_by, status, created_at, updated_at,
        ) = row

        stats['records_fetched'] += 1

        station = station_map.get(station_id)
        if not station:
            stats['records_skipped'] += 1
            stats['errors'].append(
                f'meter_check_schedule {schedule_id}: station {station_id} not in Raven'
            )
            continue

        fields = dict(
            station             = station,
            week_label          = week_label,
            scheduled_date      = scheduled_date,
            assigned_by         = assigned_by or '',
            status              = status,
            datanest_created_at = _aware(created_at),
            datanest_updated_at = _aware(updated_at),
        )

        try:
            if schedule_id in existing:
                obj = existing[schedule_id]
                for k, v in fields.items():
                    setattr(obj, k, v)
                obj.save()
                stats['records_updated'] += 1
            else:
                EAMeterCheckSchedule.objects.create(datanest_id=schedule_id, **fields)
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'meter_check_schedule {schedule_id}: {exc}')

    return stats
