"""
energy_account/sync/weekly_readings.py

Incremental sync: DataNest `ea_weekly_readings` → Raven `EAWeeklyReading`.

Mapping:
  station_id → InjectionSubstation.slug
  feeder_id  → Feeder.slug
"""

from django.db import connections
from django.utils.timezone import is_naive, make_aware

from common.models import Feeder, InjectionSubstation
from energy_account.models import EAGridMeter, EAWeeklyReading
from technical.sync.base import get_sync_window


def _aware(dt):
    if dt and is_naive(dt):
        return make_aware(dt)
    return dt


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_weekly_readings')

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

    station_map    = {s.slug: s for s in InjectionSubstation.objects.all()}
    feeder_map     = {f.slug: f for f in Feeder.objects.filter(is_onboarded=True)}
    grid_meter_map = {m.datanest_id: m for m in EAGridMeter.objects.all()}
    existing       = {r.datanest_id: r for r in EAWeeklyReading.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT weekly_reading_id, station_id, feeder_id, grid_meter_id,
                   week_start_date, week_end_date, reading_value, energy_mwh,
                   prior_week_energy_mwh, delta_pct, is_anomaly,
                   data_source, notes, created_at
            FROM ea_weekly_readings
            WHERE created_at >= %s AND created_at <= %s
            ORDER BY created_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            weekly_reading_id, station_id, feeder_id, grid_meter_id,
            week_start_date, week_end_date, reading_value, energy_mwh,
            prior_week_energy_mwh, delta_pct, is_anomaly,
            data_source, notes, created_at,
        ) = row

        stats['records_fetched'] += 1

        station = station_map.get(station_id)
        feeder  = feeder_map.get(feeder_id)

        if not station or not feeder:
            stats['records_skipped'] += 1
            stats['errors'].append(
                f'weekly_reading {weekly_reading_id}: '
                f'station={station_id} or feeder={feeder_id} not in Raven'
            )
            continue

        grid_meter = grid_meter_map.get(grid_meter_id) if grid_meter_id else None

        fields = dict(
            station               = station,
            feeder                = feeder,
            grid_meter            = grid_meter,
            week_start_date       = week_start_date,
            week_end_date         = week_end_date,
            reading_value         = reading_value,
            energy_mwh            = energy_mwh,
            prior_week_energy_mwh = prior_week_energy_mwh,
            delta_pct             = delta_pct,
            is_anomaly            = bool(is_anomaly),
            data_source           = data_source,
            notes                 = notes or '',
            datanest_created_at   = _aware(created_at),
        )

        try:
            if weekly_reading_id in existing:
                obj = existing[weekly_reading_id]
                for k, v in fields.items():
                    setattr(obj, k, v)
                obj.save()
                stats['records_updated'] += 1
            else:
                EAWeeklyReading.objects.create(datanest_id=weekly_reading_id, **fields)
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'weekly_reading {weekly_reading_id}: {exc}')

    return stats
