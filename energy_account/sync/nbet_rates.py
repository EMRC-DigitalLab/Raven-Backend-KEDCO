"""
energy_account/sync/nbet_rates.py

Full sync: DataNest `nbet_market_bilateral` → Raven `NBETMarketBilateral`.

This is a small, slow-changing table (one row per effective date).
We do a full upsert each run — no watermark needed.
"""

from django.db import connections

from energy_account.models import NBETMarketBilateral

from technical.sync.base import get_sync_window


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_nbet_rates')

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

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT rate_id, effective_date, rate_year,
                   nbet_rate, market_operator_rate, bilateral,
                   is_active, created_at, updated_at
            FROM nbet_market_bilateral
            ORDER BY effective_date ASC
        """)
        rows = cursor.fetchall()

    existing = {r.datanest_id: r for r in NBETMarketBilateral.objects.all()}

    for row in rows:
        (
            rate_id, effective_date, rate_year,
            nbet_rate, mo_rate, bilateral,
            is_active, created_at, updated_at,
        ) = row

        stats['records_fetched'] += 1

        try:
            if rate_id in existing:
                obj = existing[rate_id]
                changed = (
                    obj.effective_date        != effective_date
                    or obj.nbet_rate          != nbet_rate
                    or obj.market_operator_rate != mo_rate
                    or obj.bilateral          != bilateral
                    or obj.is_active          != bool(is_active)
                )
                if changed:
                    obj.effective_date        = effective_date
                    obj.rate_year             = rate_year
                    obj.nbet_rate             = nbet_rate
                    obj.market_operator_rate  = mo_rate
                    obj.bilateral             = bilateral
                    obj.is_active             = bool(is_active)
                    obj.datanest_created_at   = created_at
                    obj.datanest_updated_at   = updated_at
                    obj.save()
                    stats['records_updated'] += 1
                else:
                    stats['records_skipped'] += 1
            else:
                NBETMarketBilateral.objects.create(
                    datanest_id           = rate_id,
                    effective_date        = effective_date,
                    rate_year             = rate_year,
                    nbet_rate             = nbet_rate,
                    market_operator_rate  = mo_rate,
                    bilateral             = bilateral,
                    is_active             = bool(is_active),
                    datanest_created_at   = created_at,
                    datanest_updated_at   = updated_at,
                )
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'nbet rate_id={rate_id}: {exc}')

    return stats
