"""
energy_account/sync/monthly_returns.py

Incremental sync: DataNest `ea_monthly_returns` → Raven `EAMonthlyReturn`.

Mapping:
  station_id → InjectionSubstation.slug  (exact match — confirmed from live data)
"""

from django.db import connections
from django.utils.timezone import is_naive, make_aware

from common.models import InjectionSubstation
from energy_account.models import EAMonthlyReturn
from technical.sync.base import get_sync_window


def _aware(dt):
    if dt and is_naive(dt):
        return make_aware(dt)
    return dt


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_monthly_returns')

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
    existing    = {r.datanest_id: r for r in EAMonthlyReturn.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT return_id, station_id, month, year, status,
                   deviation_threshold_pct, submitted_at,
                   is_late, late_reason,
                   station_billing_mwh, station_billing_naira,
                   created_at, updated_at
            FROM ea_monthly_returns
            WHERE updated_at >= %s AND updated_at <= %s
            ORDER BY created_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            return_id, station_id, month, year, status,
            deviation_threshold_pct, submitted_at,
            is_late, late_reason,
            station_billing_mwh, station_billing_naira,
            created_at, updated_at,
        ) = row

        stats['records_fetched'] += 1

        station = station_map.get(station_id)
        if not station:
            stats['records_skipped'] += 1
            stats['errors'].append(f'monthly_return {return_id}: station slug "{station_id}" not found in Raven')
            continue

        try:
            if return_id in existing:
                obj = existing[return_id]
                obj.station                 = station
                obj.month                   = month
                obj.year                    = year
                obj.status                  = status
                obj.deviation_threshold_pct = deviation_threshold_pct
                obj.submitted_at            = _aware(submitted_at)
                obj.is_late                 = bool(is_late)
                obj.late_reason             = late_reason or ''
                obj.station_billing_mwh     = station_billing_mwh
                obj.station_billing_naira   = station_billing_naira
                obj.datanest_created_at     = _aware(created_at)
                obj.datanest_updated_at     = _aware(updated_at)
                obj.save()
                stats['records_updated'] += 1
            else:
                EAMonthlyReturn.objects.create(
                    datanest_id             = return_id,
                    station                 = station,
                    month                   = month,
                    year                    = year,
                    status                  = status,
                    deviation_threshold_pct = deviation_threshold_pct,
                    submitted_at            = _aware(submitted_at),
                    is_late                 = bool(is_late),
                    late_reason             = late_reason or '',
                    station_billing_mwh     = station_billing_mwh,
                    station_billing_naira   = station_billing_naira,
                    datanest_created_at     = _aware(created_at),
                    datanest_updated_at     = _aware(updated_at),
                )
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'monthly_return {return_id}: {exc}')

    return stats
