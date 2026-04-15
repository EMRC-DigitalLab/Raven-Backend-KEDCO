"""
energy_account/sync/coupling_log.py

Incremental sync: DataNest `ea_coupling_log` → Raven `EACouplingLog`.

Mapping:
  station_id               → InjectionSubstation.slug
  faulted_transformer_id   → PowerTransformer.slug
  substitute_transformer_id→ PowerTransformer.slug
"""

from django.db import connections
from django.utils.timezone import is_naive, make_aware

from common.models import InjectionSubstation, PowerTransformer
from energy_account.models import EACouplingLog
from technical.sync.base import get_sync_window


def _aware(dt):
    if dt and is_naive(dt):
        return make_aware(dt)
    return dt


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_coupling_log')

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

    station_map     = {s.slug: s for s in InjectionSubstation.objects.all()}
    transformer_map = {t.slug: t for t in PowerTransformer.objects.all()}
    existing        = {c.datanest_id: c for c in EACouplingLog.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT coupling_log_id, station_id, billing_month, billing_year,
                   faulted_transformer_id, substitute_transformer_id,
                   coupling_start, coupling_end, affected_feeder_ids,
                   coupling_type, billing_impact_notes, created_at, updated_at
            FROM ea_coupling_log
            WHERE updated_at >= %s AND updated_at <= %s
            ORDER BY created_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            coupling_log_id, station_id, billing_month, billing_year,
            faulted_transformer_id, substitute_transformer_id,
            coupling_start, coupling_end, affected_feeder_ids,
            coupling_type, billing_impact_notes, created_at, updated_at,
        ) = row

        stats['records_fetched'] += 1

        station = station_map.get(station_id)
        if not station:
            stats['records_skipped'] += 1
            stats['errors'].append(f'coupling_log {coupling_log_id}: station {station_id} not in Raven')
            continue

        faulted_transformer    = transformer_map.get(faulted_transformer_id) if faulted_transformer_id else None
        substitute_transformer = transformer_map.get(substitute_transformer_id) if substitute_transformer_id else None

        # affected_feeder_ids arrives as a JSON list from MySQL
        if not isinstance(affected_feeder_ids, list):
            affected_feeder_ids = []

        fields = dict(
            station                = station,
            billing_month          = billing_month,
            billing_year           = billing_year,
            faulted_transformer    = faulted_transformer,
            substitute_transformer = substitute_transformer,
            coupling_start         = _aware(coupling_start),
            coupling_end           = _aware(coupling_end),
            affected_feeder_ids    = affected_feeder_ids,
            coupling_type          = coupling_type,
            billing_impact_notes   = billing_impact_notes or '',
            datanest_created_at    = _aware(created_at),
            datanest_updated_at    = _aware(updated_at),
        )

        try:
            if coupling_log_id in existing:
                obj = existing[coupling_log_id]
                for k, v in fields.items():
                    setattr(obj, k, v)
                obj.save()
                stats['records_updated'] += 1
            else:
                EACouplingLog.objects.create(datanest_id=coupling_log_id, **fields)
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'coupling_log {coupling_log_id}: {exc}')

    return stats
