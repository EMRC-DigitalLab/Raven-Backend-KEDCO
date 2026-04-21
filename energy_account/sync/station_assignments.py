"""
energy_account/sync/station_assignments.py

Incremental sync: DataNest `ea_station_assignments` → Raven `EAStationAssignment`.

Mapping:
  station_id → InjectionSubstation.slug
  user_id    → stored as datanest_user_id (no FK — DataNest user PKs differ from Raven)
"""

from django.db import connections
from django.utils.timezone import is_naive, make_aware

from common.models import InjectionSubstation
from energy_account.models import EAStationAssignment
from technical.sync.base import get_sync_window


def _aware(dt):
    if dt and is_naive(dt):
        return make_aware(dt)
    return dt


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_station_assignments')

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
    existing    = {a.datanest_id: a for a in EAStationAssignment.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT assignment_id, user_id, station_id,
                   assigned_by, assigned_at, status, updated_at
            FROM ea_station_assignments
            WHERE updated_at >= %s AND updated_at <= %s
            ORDER BY assigned_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            assignment_id, user_id, station_id,
            assigned_by, assigned_at, status, updated_at,
        ) = row

        stats['records_fetched'] += 1

        station = station_map.get(station_id)
        if not station:
            stats['records_skipped'] += 1
            stats['errors'].append(
                f'station_assignment {assignment_id}: station {station_id} not in Raven'
            )
            continue

        fields = dict(
            station             = station,
            datanest_user_id    = user_id or '',
            assigned_by         = assigned_by or '',
            assigned_at         = _aware(assigned_at),
            status              = status,
            datanest_updated_at = _aware(updated_at),
        )

        try:
            if assignment_id in existing:
                obj = existing[assignment_id]
                for k, v in fields.items():
                    setattr(obj, k, v)
                obj.save()
                stats['records_updated'] += 1
            else:
                EAStationAssignment.objects.create(datanest_id=assignment_id, **fields)
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'station_assignment {assignment_id}: {exc}')

    return stats
