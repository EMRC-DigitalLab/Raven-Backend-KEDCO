"""
energy_account/sync/grid_meters.py

Incremental sync: DataNest `ea_grid_meters` → Raven `EAGridMeter`.

Mapping:
  transformer_id → PowerTransformer.slug
  feeder_id      → Feeder.slug
"""

from django.db import connections

from common.models import Feeder, PowerTransformer
from energy_account.models import EAGridMeter
from technical.sync.base import get_sync_window


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_grid_meters')

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

    transformer_map = {t.slug: t for t in PowerTransformer.objects.all()}
    feeder_map      = {f.slug: f for f in Feeder.objects.filter(is_onboarded=True)}
    existing        = {m.datanest_id: m for m in EAGridMeter.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT grid_meter_id, meter_no, multiplying_factor,
                   meter_owner_type, transformer_id, feeder_id,
                   percentage_share, status, created_at, updated_at
            FROM ea_grid_meters
            WHERE updated_at >= %s AND updated_at <= %s
            ORDER BY created_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            grid_meter_id, meter_no, multiplying_factor,
            meter_owner_type, transformer_id, feeder_id,
            percentage_share, status, created_at, updated_at,
        ) = row

        stats['records_fetched'] += 1

        transformer = transformer_map.get(transformer_id) if transformer_id else None
        feeder      = feeder_map.get(feeder_id) if feeder_id else None

        try:
            if grid_meter_id in existing:
                obj = existing[grid_meter_id]
                obj.meter_no            = meter_no
                obj.multiplying_factor  = multiplying_factor
                obj.meter_owner_type    = meter_owner_type
                obj.transformer         = transformer
                obj.feeder              = feeder
                obj.percentage_share    = percentage_share
                obj.status              = status
                obj.datanest_created_at = created_at
                obj.datanest_updated_at = updated_at
                obj.save()
                stats['records_updated'] += 1
            else:
                EAGridMeter.objects.create(
                    datanest_id         = grid_meter_id,
                    meter_no            = meter_no,
                    multiplying_factor  = multiplying_factor,
                    meter_owner_type    = meter_owner_type,
                    transformer         = transformer,
                    feeder              = feeder,
                    percentage_share    = percentage_share,
                    status              = status,
                    datanest_created_at = created_at,
                    datanest_updated_at = updated_at,
                )
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'grid_meter_id={grid_meter_id}: {exc}')

    return stats
