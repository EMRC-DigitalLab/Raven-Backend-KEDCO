"""
energy_account/sync/meter_check_records.py

Incremental sync: DataNest `ea_meter_check_records` → Raven `EAMeterCheckRecord`.

Depends on: EAMeterCheckSchedule, InjectionSubstation.
"""

from django.db import connections
from django.utils.timezone import is_naive, make_aware

from common.models import InjectionSubstation
from energy_account.models import EAMeterCheckRecord, EAMeterCheckSchedule
from technical.sync.base import get_sync_window


def _aware(dt):
    if dt and is_naive(dt):
        return make_aware(dt)
    return dt


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_meter_check_records')

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

    schedule_map = {s.datanest_id: s for s in EAMeterCheckSchedule.objects.all()}
    station_map  = {s.slug: s for s in InjectionSubstation.objects.all()}
    existing     = {r.datanest_id: r for r in EAMeterCheckRecord.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT check_id, schedule_id, station_id, checked_by, check_date,
                   polarity, power_factor, phase_angle,
                   voltage_r, voltage_y, voltage_b,
                   current_reading, import_reading, export_reading,
                   ct_ratio, battery_level, meter_number_verified,
                   issues_observed, risk_level, remarks, created_at
            FROM ea_meter_check_records
            WHERE created_at >= %s AND created_at <= %s
            ORDER BY created_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            check_id, schedule_id, station_id, checked_by, check_date,
            polarity, power_factor, phase_angle,
            voltage_r, voltage_y, voltage_b,
            current_reading, import_reading, export_reading,
            ct_ratio, battery_level, meter_number_verified,
            issues_observed, risk_level, remarks, created_at,
        ) = row

        stats['records_fetched'] += 1

        schedule = schedule_map.get(schedule_id)
        station  = station_map.get(station_id)

        if not schedule or not station:
            stats['records_skipped'] += 1
            stats['errors'].append(
                f'meter_check_record {check_id}: '
                f'schedule={schedule_id} or station={station_id} not in Raven'
            )
            continue

        if check_id in existing:
            stats['records_skipped'] += 1
            continue

        try:
            EAMeterCheckRecord.objects.create(
                datanest_id           = check_id,
                schedule              = schedule,
                station               = station,
                check_date            = check_date,
                polarity              = polarity,
                power_factor          = power_factor,
                phase_angle           = phase_angle,
                voltage_r             = voltage_r,
                voltage_y             = voltage_y,
                voltage_b             = voltage_b,
                current_reading       = current_reading,
                import_reading        = import_reading,
                export_reading        = export_reading,
                ct_ratio              = ct_ratio or '',
                battery_level         = battery_level,
                meter_number_verified = bool(meter_number_verified),
                issues_observed       = issues_observed or '',
                risk_level            = risk_level,
                remarks               = remarks or '',
                datanest_created_at   = _aware(created_at),
            )
            stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'meter_check_record {check_id}: {exc}')

    return stats
