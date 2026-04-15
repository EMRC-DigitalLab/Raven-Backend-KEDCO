"""
energy_account/sync/feeder_technical_energy.py

Incremental sync: DataNest `ea_feeder_technical_energy` → Raven `EAFeederTechnicalEnergy`.

Mapping:
  feeder_id  → Feeder.slug
  station_id → InjectionSubstation.slug
"""

from django.db import connections
from django.utils.timezone import is_naive, make_aware

from common.models import Feeder, InjectionSubstation
from energy_account.models import EAFeederTechnicalEnergy, EAMonthlyReturn
from technical.sync.base import get_sync_window


def _aware(dt):
    if dt and is_naive(dt):
        return make_aware(dt)
    return dt


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_feeder_technical_energy')

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

    return_map  = {r.datanest_id: r for r in EAMonthlyReturn.objects.all()}
    feeder_map  = {f.slug: f for f in Feeder.objects.filter(is_onboarded=True)}
    station_map = {s.slug: s for s in InjectionSubstation.objects.all()}
    existing    = {e.datanest_id: e for e in EAFeederTechnicalEnergy.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT technical_energy_id, return_id, feeder_id, feeder_type,
                   station_id, energy_mwh, data_completeness_pct,
                   days_with_data, total_days_in_period, has_gaps, gap_notes,
                   data_source, populated_at
            FROM ea_feeder_technical_energy
            WHERE populated_at >= %s AND populated_at <= %s
            ORDER BY populated_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            tech_energy_id, return_id, feeder_id, feeder_type,
            station_id, energy_mwh, data_completeness_pct,
            days_with_data, total_days_in_period, has_gaps, gap_notes,
            data_source, populated_at,
        ) = row

        stats['records_fetched'] += 1

        monthly_return = return_map.get(return_id)
        feeder         = feeder_map.get(feeder_id)
        station        = station_map.get(station_id)

        if not monthly_return or not feeder or not station:
            stats['records_skipped'] += 1
            stats['errors'].append(
                f'feeder_technical_energy {tech_energy_id}: '
                f'missing return={return_id}, feeder={feeder_id}, or station={station_id}'
            )
            continue

        fields = dict(
            monthly_return        = monthly_return,
            feeder                = feeder,
            feeder_type           = feeder_type,
            station               = station,
            energy_mwh            = energy_mwh,
            data_completeness_pct = data_completeness_pct,
            days_with_data        = days_with_data,
            total_days_in_period  = total_days_in_period,
            has_gaps              = bool(has_gaps),
            gap_notes             = gap_notes or '',
            data_source           = data_source,
            populated_at          = _aware(populated_at),
        )

        try:
            if tech_energy_id in existing:
                obj = existing[tech_energy_id]
                for k, v in fields.items():
                    setattr(obj, k, v)
                obj.save()
                stats['records_updated'] += 1
            else:
                EAFeederTechnicalEnergy.objects.create(datanest_id=tech_energy_id, **fields)
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'feeder_technical_energy {tech_energy_id}: {exc}')

    return stats
