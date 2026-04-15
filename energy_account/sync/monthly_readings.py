"""
energy_account/sync/monthly_readings.py

Incremental sync: DataNest `ea_monthly_readings` → Raven `EAMonthlyReading`.

Depends on: EAMonthlyReturn, EAGridMeter, PowerTransformer, Feeder.
"""

from django.db import connections
from django.utils.timezone import is_naive, make_aware

from common.models import Feeder, PowerTransformer
from energy_account.models import EAGridMeter, EAMonthlyReading, EAMonthlyReturn
from technical.sync.base import get_sync_window


def _aware(dt):
    if dt and is_naive(dt):
        return make_aware(dt)
    return dt


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_monthly_readings')

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

    return_map      = {r.datanest_id: r for r in EAMonthlyReturn.objects.all()}
    grid_meter_map  = {m.datanest_id: m for m in EAGridMeter.objects.all()}
    transformer_map = {t.slug: t for t in PowerTransformer.objects.all()}
    feeder_map      = {f.slug: f for f in Feeder.objects.filter(is_onboarded=True)}
    existing        = {r.datanest_id: r for r in EAMonthlyReading.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT reading_id, return_id, grid_meter_id,
                   transformer_id, feeder_id,
                   previous_reading, present_reading, energy_mwh,
                   deviation_pct, is_flagged, remarks, data_source,
                   billing_mwh, billing_rule_applied, billing_naira,
                   nbet_rate_snapshot, created_at, updated_at
            FROM ea_monthly_readings
            WHERE updated_at >= %s AND updated_at <= %s
            ORDER BY created_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            reading_id, return_id, grid_meter_id,
            transformer_id, feeder_id,
            previous_reading, present_reading, energy_mwh,
            deviation_pct, is_flagged, remarks, data_source,
            billing_mwh, billing_rule_applied, billing_naira,
            nbet_rate_snapshot, created_at, updated_at,
        ) = row

        stats['records_fetched'] += 1

        monthly_return = return_map.get(return_id)
        grid_meter     = grid_meter_map.get(grid_meter_id)

        if not monthly_return or not grid_meter:
            stats['records_skipped'] += 1
            stats['errors'].append(
                f'monthly_reading {reading_id}: '
                f'missing return={return_id} or grid_meter={grid_meter_id}'
            )
            continue

        transformer = transformer_map.get(transformer_id) if transformer_id else None
        feeder      = feeder_map.get(feeder_id) if feeder_id else None

        fields = dict(
            monthly_return       = monthly_return,
            grid_meter           = grid_meter,
            transformer          = transformer,
            feeder               = feeder,
            previous_reading     = previous_reading,
            present_reading      = present_reading,
            energy_mwh           = energy_mwh,
            deviation_pct        = deviation_pct,
            is_flagged           = bool(is_flagged),
            remarks              = remarks or '',
            data_source          = data_source,
            billing_mwh          = billing_mwh,
            billing_rule_applied = billing_rule_applied,
            billing_naira        = billing_naira,
            nbet_rate_snapshot   = nbet_rate_snapshot,
            datanest_created_at  = _aware(created_at),
            datanest_updated_at  = _aware(updated_at),
        )

        try:
            if reading_id in existing:
                obj = existing[reading_id]
                for k, v in fields.items():
                    setattr(obj, k, v)
                obj.save()
                stats['records_updated'] += 1
            else:
                EAMonthlyReading.objects.create(datanest_id=reading_id, **fields)
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'monthly_reading {reading_id}: {exc}')

    return stats
